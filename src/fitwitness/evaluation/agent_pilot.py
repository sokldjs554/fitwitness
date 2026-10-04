"""Paid, operator-only full graph smoke/ablation. No gold enters the graph."""
import json, os, subprocess, time
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from fitwitness.contracts import Budget, DrawingRevision, RunRequest, SearchRequest, TenantScope
from fitwitness.ingest.pdf import extract_pdf
from fitwitness.storage.repository import Repository
from fitwitness.runtime.jobs import Jobs
from fitwitness.agents.graph import execute_run

PROTOCOL = {
    'scope': 'Full LangGraph tool-use smoke/ablation; one synthetic family, two repeated queries, not a general performance benchmark',
    'pdf_cap_usd': '0.60',
    'model': 'claude-haiku-4-5-20251001',
    'mode_labels': {'fixed': 'single-pass tool planner', 'react': 'iterative tool planner', 'fitwitness': 'planner plus separate challenger'},
    'runs': [{'mode': m, 'repeat': i, 'cap_usd': '0.066'} for i in range(2) for m in ('fixed','react','fitwitness')],
    'query': '브래킷 구멍 간격 40mm SUS304',
    'family': 'FW-F000',
}


def score(expected, actual):
    correct = sum(actual.get(k) == v for k, v in expected.items())
    return {'correct': correct, 'total': len(expected), 'accuracy': correct / len(expected)}


def build_request(config):
    provider=config.get('provider','anthropic')
    return RunRequest(search=SearchRequest(text=PROTOCOL['query']),mode=config['mode'],provider=provider,
                           model_id=PROTOCOL['model'] if provider!='rules' else 'rules',
                           budget=Budget(max_cost_usd=Decimal(config['cap_usd']),max_tokens=64000,max_model_calls=4,deadline_seconds=240))


def run(output, diagnostic=False):
    out=Path(output); out.mkdir(parents=True,exist_ok=False)
    source_paths = [*Path('src/fitwitness/agents').glob('*.py'), Path(__file__)]
    configs = PROTOCOL['runs'][:3] if diagnostic else PROTOCOL['runs']
    protocol={**PROTOCOL, 'runs':configs, 'phase':'v2-debug' if diagnostic else 'v1',
              'prior_measured_cost_usd':'0.171018' if diagnostic else None, 'code_sha':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
              'source_hashes': {str(p):sha256(p.read_bytes()).hexdigest() for p in source_paths}}
    (out/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2))
    repo=Repository(os.environ['FITWITNESS_DATABASE_URL']); jobs=Jobs(repo)
    scope=TenantScope(tenant_id=str(uuid4()),user_id='operator',role='admin')
    root=Path('var/corpus'); manifest=json.loads((root/'manifest.json').read_text())
    entries=[e for e in manifest['document_entries'] if e['family_id']==PROTOCOL['family'] and not e['is_revision_update']]
    gold=json.loads((root/'gold/labels.json').read_text())
    expected={}
    for e in entries:
        rev=DrawingRevision(tenant_id=scope.tenant_id,**{k:e[k] for k in ('id','document_id','drawing_number','family_id','revision_label','supersedes','kind','title','source_hash')})
        data=(root/e['pdf']).read_bytes()
        repo.add_revision(scope,rev,data); repo.save_facts(scope,rev.id,extract_pdf(data,rev))
        truth=gold[rev.id]
        expected[rev.id]='mismatch' if truth['hole_spacing']!=40 or truth['material'] not in ('SUS304',None) else 'unknown' if truth['material'] is None else 'match'
    (out/'sources.json').write_text(json.dumps({'sources':[{k:e[k] for k in ('id','source_hash')} for e in entries],'expected':expected},indent=2))
    rows=[]
    # rules reference is executed, not synthesized from expected labels.
    configs=[{'mode':'fixed','repeat':0,'cap_usd':'0.01','provider':'rules'}, *configs]
    for config in configs:
        provider=config.get('provider','anthropic')
        request=build_request(config)
        job=jobs.enqueue(scope,request,str(uuid4())); started=time.perf_counter(); error=None
        try: execute_run(repo,scope,job.id)
        except Exception as exc: error=type(exc).__name__+': '+str(exc)
        view=jobs.get(scope,job.id); events=jobs.events(scope,job.id)
        actual={d.revision_id:d.verdict.value for d in view.decisions}
        row={**config,'provider':provider,'run_id':job.id,'state':view.state,'error':error,
             'latency_ms':(time.perf_counter()-started)*1000,'metrics':score(expected,actual),
             'result':view.model_dump(mode='json'),'events':events}
        rows.append(row)
        with (out/'runs.jsonl').open('a') as f: f.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
        print(provider,config['mode'],config['repeat'],view.state,row['metrics'],flush=True)
    report={'status':'measured','created_at':datetime.now(timezone.utc).isoformat(),'protocol':protocol,'runs':rows,
            'limitations':['동일 합성 family의 단일 질문을 두 번 실행한 연결 검증입니다. 일반 성능 우위를 입증하지 않습니다.',
                            '최종 판정은 결정적 검증기가 담당하며 모델은 도구 선택만 수행합니다.',
                            '도번·BM25 검색을 사용합니다. 의미·이미지 검색과 VLM은 이 실험에 포함되지 않습니다.']}
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,default=str,indent=2))
    return report


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--diagnostic',action='store_true')
    args=p.parse_args();run(args.output,args.diagnostic)
