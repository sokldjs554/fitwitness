"""Bounded actual VLM pilot. Original/omitted labels; no values in model input."""
import argparse,base64,gzip,json,os,subprocess,time
from collections import defaultdict
from decimal import Decimal,DecimalException
from hashlib import sha256
from pathlib import Path
from statistics import mean
from uuid import uuid4
from fitwitness.contracts import Budget,TenantScope,RunRequest,SearchRequest
from fitwitness.agents.budget import BudgetTracker
from fitwitness.agents.providers import create_model
from fitwitness.ingest.vision import validated_png
from fitwitness.evaluation.metrics import percentile

PROMPT='Read only the width and material annotations visible in this drawing. Use null for an absent or unreadable field. Do not estimate dimensions from shape.'
FACTORS={'mm':Decimal(1),'cm':Decimal(10),'m':Decimal(1000),'in':Decimal('25.4')}


def field_scores(reading,expected):
    scores={}
    for field,truth in expected.items():
        values=[]
        for a in reading.annotations:
            if a.field!=field or a.value is None:continue
            value=a.value.strip().upper()
            if field=='width':
                try:
                    value=Decimal(value)*FACTORS[a.unit]
                    if not value.is_finite():value='invalid'
                except (DecimalException,KeyError):value='invalid'
            values.append(value)
        target=Decimal(str(truth)) if field=='width' and truth is not None else truth
        scores[field]=(not values) if target is None else bool(values) and all(v==target for v in values)
    return scores


def make_cases(root):
    m=json.loads((root/'manifest.json').read_text());gold=json.loads((root/'gold/labels.json').read_text())
    cases=[]
    for family in sorted(m['family_splits']['dev'])[:2]:
        entries=[d for d in m['document_entries'] if d['family_id']==family and not d['is_revision_update']]
        for variant,suffix in [('original','-0'),('missing_material','-3'),('no_annotations','-0')]:
            d=next(d for d in entries if d['drawing_number'].endswith(suffix))
            original=(root/d['png']).read_bytes()
            region=(.1,.13,.85,.55) if variant=='no_annotations' else (0,0,1,1)
            image=validated_png(original,region)
            expected={'width':None,'material':None} if variant=='no_annotations' else {
                'width':str(gold[d['id']]['width']),'material':gold[d['id']]['material']}
            cases.append(dict(id=family+'-'+variant,family_id=family,variant=variant,revision_id=d['id'],
                source_image_hash=sha256(original).hexdigest(),image_hash=sha256(image).hexdigest(),
                expected=expected,image_data_url='data:image/png;base64,'+base64.b64encode(image).decode()))
    return cases


def run(root,output):
    output.mkdir(parents=True,exist_ok=False)
    cases=make_cases(root)
    protocol=dict(version='vision-v1',repeats=3,fields=['width','material'],cases=cases,
        prompt=PROMPT,model='claude-haiku-4-5-20251001',call_cap=18,per_case_usd='0.025',graph_cap_usd='0.10',
        maximum_new_usd='0.55',prior_measured_usd='0.187841',maximum_aggregate_usd='0.737841',
        scope='2 dev families, original/missing material/annotation-free crop; diagnostic, not independent industrial evaluation')
    (output/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2))
    rows=[]
    with (output/'runs.jsonl').open('w') as stream:
        for repeat in range(3):
            for case in cases:
                tracker=BudgetTracker(Budget(max_model_calls=1,max_tokens=64000,max_cost_usd=Decimal('.025')))
                journal=output/f"budget-{case['id']}-{repeat+1}.json"
                tracker.persist=lambda usage,p=journal:p.write_text(usage.model_dump_json(indent=2))
                model=create_model('anthropic',protocol['model'],tracker)
                events=[];model.emit=lambda kind,data:events.append(dict(kind=kind,payload=data))
                reading=None;metadata=None;error=None;start=time.perf_counter()
                try:reading,metadata=model.read_image(base64.b64decode(case['image_data_url'].split(',')[1]),PROMPT)
                except Exception as exc:error=f'{type(exc).__name__}: {exc}'
                row=dict(case_id=case['id'],repeat=repeat+1,status='error' if error else 'ok',error=error,
                    reading=reading.model_dump(mode='json') if reading else None,metadata=metadata,events=events,
                    fields=field_scores(reading,case['expected']) if reading else {k:False for k in case['expected']},
                    latency_ms=(time.perf_counter()-start)*1000,usage=tracker.usage.model_dump(mode='json'))
                rows.append(row);stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush()
                print(case['id'],repeat+1,row['status'],flush=True)
    # Real graph, one deliberately image-only evidence record: no parsed facts.
    from fitwitness.storage.repository import Repository
    from fitwitness.data.bootstrap import seed
    from fitwitness.runtime.jobs import Jobs
    from fitwitness.agents.graph import execute_run
    repo=Repository(os.environ['FITWITNESS_DATABASE_URL']);repo.migrate();jobs=Jobs(repo);jobs.migrate()
    scope=TenantScope(tenant_id='vision-'+str(uuid4()),user_id='evaluation');seed(repo,scope,root)
    source=cases[0];revision=repo.get_revision(scope,source['revision_id'])
    repo.save_facts(scope,revision.id,[])
    request=RunRequest(provider='anthropic',model_id=protocol['model'],mode='react',
        search=SearchRequest(text=f"{revision.drawing_number} 원본 이미지의 너비 주석을 read_image_region으로 읽어줘. 폭 {source['expected']['width']}mm",top_k=1),
        budget=Budget(max_model_calls=4,max_tool_calls=6,max_tokens=64000,max_cost_usd=Decimal('.10')))
    job=jobs.enqueue(scope,request,str(uuid4()));os.environ['FITWITNESS_VISION']='enabled'
    graph_error=None
    try:execute_run(repo,scope,job.id)
    except Exception as exc:graph_error=f'{type(exc).__name__}: {exc}'
    view=jobs.get(scope,job.id);events=jobs.events(scope,job.id)
    graph=dict(run=view.model_dump(mode='json'),events=events,error=graph_error,
        visual_calls=sum(e['kind']=='model' and e['payload'].get('role')=='vision' for e in events),
        limitation='vector facts intentionally removed for one synthetic drawing; visual observations remain uncertain')
    (output/'graph.json').write_text(json.dumps(graph,ensure_ascii=False,indent=2,default=str))
    summarize_saved(output,os.getenv('GITHUB_SHA') or subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())


def summarize_saved(output,code_sha):
    """Recover a report from persisted records without DB or model calls."""
    protocol=json.loads((output/'protocol.json').read_text())
    rows=[json.loads(line) for line in (output/'runs.jsonl').read_text().splitlines()]
    graph=json.loads((output/'graph.json').read_text())
    usage=graph['run']['usage']
    known=sum(Decimal(r['usage']['cost_usd'] or '0') for r in rows)+Decimal(str(usage['cost_usd'] or 0))
    reserved=sum(Decimal(r['usage']['reserved_cost_usd']) for r in rows)+Decimal(str(usage['reserved_cost_usd']))
    report=dict(status='measured',protocol=protocol,rows=rows,graph=graph,
        code_sha=code_sha,
        metrics=dict(attempts=len(rows),completed=sum(r['status']=='ok' for r in rows),
            field_accuracy=mean(v for r in rows for v in r['fields'].values()),
            latency_p50_ms=percentile([r['latency_ms'] for r in rows],.5),latency_p95_ms=percentile([r['latency_ms'] for r in rows],.95),
            known_cost_usd=str(known),unresolved_reserved_usd=str(reserved)),
        raw_sha256=sha256((output/'runs.jsonl').read_bytes()).hexdigest(),
        limitations=['2개 dev family의 합성 PNG 6종×3회입니다. 독립 산업 도면·스캔 OCR 일반화 평가가 아닙니다.',
            '값/단위와 누락 필드만 평가했습니다. bbox는 모델의 제안이며 위치 정확도는 검증하지 않았습니다.',
            'VLM 관측은 uncertain으로 보관하며 단독으로 조건 일치를 확정하지 않습니다.',
            'Agent 진단은 PDF 추출 사실을 의도적으로 지운 도면 한 개입니다. 자동 도구 선택의 일반적 우위를 뜻하지 않습니다.'])
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    with gzip.GzipFile(filename=str(output/'runs.jsonl.gz'),mode='wb',mtime=0) as f:f.write((output/'runs.jsonl').read_bytes())
    print(json.dumps(report['metrics']),flush=True)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('var/corpus'));p.add_argument('--output',type=Path,required=True)
    p.add_argument('--summarize-only',action='store_true');p.add_argument('--execution-sha')
    a=p.parse_args()
    if a.summarize_only:
        if not a.execution_sha:p.error('--execution-sha is required for offline recovery')
        summarize_saved(a.output,a.execution_sha)
    else:run(a.root,a.output)
