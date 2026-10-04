"""Frozen synthetic retrieval pilot, real PostgreSQL and pinned encoders. No LLM calls."""
from collections import defaultdict
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from statistics import mean
from uuid import uuid4
import argparse, base64, gzip, json, math, os, platform, subprocess, time
from PIL import Image
from fitwitness.evaluation.metrics import percentile

METHODS = {'lexical': ('도번 + BM25', {'exact','bm25'}),
           'semantic': ('E5 의미 검색', {'semantic'}),
           'image': ('OpenCLIP 이미지', {'image'}),
           'hybrid': ('복합 검색 · RRF', {'exact','bm25','semantic','image'})}
PARAPHRASES = {'bracket':'장비를 바닥에 붙잡아 두는 납작한 금속 부속',
              'flange':'관 끝에 맞대어 체결하는 둥근 금속 부속',
              'shaft':'동력을 전달하며 돌아가는 긴 원통형 부속',
              'housing':'기계 내부 부속을 둘러싸는 속이 빈 외피'}


def score_ranking(ranked, relevance, k=5):
    if not relevance:
        return {'recall': None, 'ndcg': None}
    ranked=list(dict.fromkeys(ranked))[:k]
    relevant={rid for rid,gain in relevance.items() if gain>0}
    if not relevant:
        return {'recall': None, 'ndcg': None}
    dcg=sum((2**relevance.get(rid,0)-1)/math.log2(i+2) for i,rid in enumerate(ranked))
    ideal=sum((2**gain-1)/math.log2(i+2) for i,gain in enumerate(sorted(relevance.values(),reverse=True)[:k]))
    return {'recall':len(set(ranked)&relevant)/len(relevant),'ndcg':dcg/ideal}


def make_cases(manifest, gold):
    active=[d for d in manifest['document_entries'] if not d['is_revision_update']]
    cases=[]
    for family in sorted(manifest['family_splits']['test']):
        source=next(d for d in active if d['family_id']==family)
        truth=gold[source['id']]
        # Image relevance includes visible spacing; text only states kind+width.
        # Gold is independent of runtime facts. Material is irrelevant to lookup.
        qrels={d['id']:1 for d in active if d['kind']==source['kind']
               and gold[d['id']]['width']==truth['width']
               and gold[d['id']].get('hole_spacing')==truth.get('hole_spacing')}
        text_qrels={d['id']:1 for d in active if d['kind']==source['kind']
                    and gold[d['id']]['width']==truth['width']}
        for category in ('exact','paraphrase','image','mixed'):
            query=(source['drawing_number'] if category=='exact' else
                   '' if category=='image' else f"{PARAPHRASES[source['kind']]} 외형 폭 {truth['width']}mm")
            cases.append(dict(id=family+'-'+category,family_id=family,category=category,text=query,
                              image_source=source['id'] if category in ('image','mixed') else None,
                              relevance={source['id']:1} if category=='exact' else
                              text_qrels if category=='paraphrase' else qrels))
    return cases


def protocol(cases):
    return dict(version='retrieval-v2',scope='합성 도면 검색 pilot',repeats=3,rrf_k=60,top_k=10,
                recall_k=5,ndcg_k=10,methods=list(METHODS),cases=json.loads(json.dumps(cases)),
                dataset_hash=sha256(json.dumps(cases,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                query_transform='top-view crop [0.10,0.13,0.85,0.55], resize 240x143, rotate 3deg white fill',
                tuning='none; held-out family queries; no selection after test metrics',
                relevance='exact: exact ID; paraphrase: kind+width; image/mixed: kind+width+hole spacing; generator gold',
                empty_relevance='excluded with explicit count; pilot has no empty-relevance cases')


def query_image(data):
    im=Image.open(BytesIO(data)).convert('RGB');w,h=im.size
    im=im.crop((int(w*.1),int(h*.13),int(w*.85),int(h*.55)))
    im=im.resize((240,143)).rotate(3,resample=Image.Resampling.BICUBIC,fillcolor='white')
    out=BytesIO();im.save(out,format='PNG');return out.getvalue()


def summarize_retrieval(rows):
    return dict(attempts=len(rows),completed=sum(r['status']=='ok' for r in rows),
                recall_at_5=mean(r['recall_at_5'] for r in rows),
                ndcg_at_10=mean(r['ndcg_at_10'] for r in rows),
                latency_p50_ms=percentile([r['latency_ms'] for r in rows],.5),
                latency_p95_ms=percentile([r['latency_ms'] for r in rows],.95))


def ablation_cases(manifest, gold):
    # Prospective selection before any new score: six never previously evaluated
    # families from the former train pool. Dev families stay separate. No fitting.
    families=sorted(manifest['family_splits']['train'],key=lambda f:sha256(('reranking-v1:'+f).encode()).hexdigest())[:6]
    copied=json.loads(json.dumps(manifest));copied['family_splits']['test']=families
    return make_cases(copied,gold)


def ablation_protocol(cases):
    p=protocol(cases)
    p.update(version='reranking-v1',candidate_pool=20,
        methods=['hybrid','cross_encoder','constraints'],
        tuning='No fitting. Six hash-selected families from former train split, excluding previous test and dev. Frozen before inference.',
        ranking='RRF top20 then BGE text cross-encoder OR deterministic required-condition ordering. Explicit drawing numbers stay first. Image-only BGE is identity.')
    return p


def run(root, output, ablation=False):
    from fitwitness.contracts import TenantScope,SearchRequest,DrawingRevision,RunRequest
    from fitwitness.data.bootstrap import seed
    from fitwitness.storage.repository import Repository
    from fitwitness.retrieval.embeddings import Encoders
    from fitwitness.retrieval.indexing import index_revision
    from fitwitness.retrieval.pipeline import search
    from fitwitness.runtime.jobs import Jobs
    from fitwitness.agents.graph import execute_run
    output.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((root/'manifest.json').read_text())
    cases=(ablation_cases if ablation else make_cases)(manifest,json.loads((root/'gold/labels.json').read_text()))
    frozen=(ablation_protocol if ablation else protocol)(cases)
    methods_spec=METHODS if not ablation else {
        'hybrid':METHODS['hybrid'],
        'cross_encoder':('복합 + BGE 재정렬',METHODS['hybrid'][1]),
        'constraints':('복합 + 조건 근거 정렬',METHODS['hybrid'][1])}
    reranker=None
    frozen['source_hashes']={d['id']:d['source_hash'] for d in manifest['document_entries'] if not d['is_revision_update']}
    frozen['source_image_hashes']={d['id']:sha256((root/d['png']).read_bytes()).hexdigest()
                                   for d in manifest['document_entries'] if not d['is_revision_update']}
    frozen['query_image_hashes']={c['id']:sha256(query_image((root/next(
        d['png'] for d in manifest['document_entries'] if d['id']==c['image_source'])).read_bytes())).hexdigest()
        for c in cases if c['image_source']}
    # Written before model loading, indexing or queries. Existing output is never overwritten.
    (output/'protocol.json').write_text(json.dumps(frozen,ensure_ascii=False,indent=2))
    repo=Repository(os.environ['FITWITNESS_DATABASE_URL']);repo.migrate();Jobs(repo).migrate()
    scope=TenantScope(tenant_id='retrieval-'+str(uuid4()),user_id='evaluation',role='operator')
    seed(repo,scope,root)
    began=time.perf_counter();encoders=Encoders();load_ms=(time.perf_counter()-began)*1000
    if ablation:
        from fitwitness.retrieval.reranking import CrossEncoder
        began=time.perf_counter();reranker=CrossEncoder();reranker_load_ms=(time.perf_counter()-began)*1000
    start=time.perf_counter()
    for i,rid in enumerate(repo.snapshot(scope).revision_ids):
        index_revision(scope,rid,repo,encoders)
        if (i+1)%25==0:print('indexed',i+1,flush=True)
    index_ms=(time.perf_counter()-start)*1000
    previews={}
    for case in cases:
        if case['image_source']:
            source=case['image_source'];qid='query-'+source
            if qid not in previews:
                image=query_image(repo.asset(scope,source,'png'));previews[qid]=image
                old=repo.get_revision(scope,source)
                query_rev=old.model_copy(update=dict(id=qid,document_id=qid,approval='draft'))
                repo.add_revision(scope,query_rev,repo.asset(scope,source,'pdf'))
                repo.put_asset(scope,qid,'png',image)
            case['query_image_id']=qid
    snapshot=repo.snapshot(scope)
    rows=[]
    # Rotate method order per repeat to avoid always favoring a warmed last method.
    with (output/'runs.jsonl').open('w') as stream:
        for repeat in range(3):
            order=list(methods_spec);order=order[repeat:]+order[:repeat]
            for case in cases:
                request=SearchRequest(text=case['text'],image_id=case.get('query_image_id'),top_k=10)
                for method in order:
                    start=time.perf_counter();ranked=[];error=None
                    try:
                        method_request=request.model_copy(update={'ranking':method if method in ('cross_encoder','constraints') else 'rrf'})
                        ranked=search(scope,method_request,snapshot,repo,encoders,methods_spec[method][1],reranker=reranker)
                    except Exception as exc:error=f'{type(exc).__name__}: {exc}'
                    elapsed=(time.perf_counter()-start)*1000
                    ids=[c.revision_id for c in ranked]
                    r=dict(case_id=case['id'],family_id=case['family_id'],category=case['category'],method=method,
                           repeat=repeat+1,status='error' if error else 'ok',error=error,latency_ms=elapsed,
                           recall_at_5=score_ranking(ids,case['relevance'],5)['recall'],
                           ndcg_at_10=score_ranking(ids,case['relevance'],10)['ndcg'],
                           ranked=[dict(revision_id=c.revision_id,scores=c.scores) for c in ranked])
                    rows.append(r);stream.write(json.dumps(r,ensure_ascii=False)+'\n');stream.flush()
            print('retrieval repeat completed',repeat+1,flush=True)
    # A real rules graph traverses the same dense tools. No paid provider or fake encoder.
    jobs=Jobs(repo);case=next(c for c in cases if c['category']=='mixed')
    job=jobs.enqueue(scope,RunRequest(search=SearchRequest(text=case['text'],image_id=case['query_image_id'],top_k=5)),str(uuid4()))
    execute_run(repo,scope,job.id,encoders=encoders)
    view=jobs.get(scope,job.id)
    graph=dict(run=view.model_dump(mode='json'),events=jobs.events(scope,job.id))
    (output/'graph.json').write_text(json.dumps(graph,ensure_ascii=False,indent=2,default=str))
    methods=[]
    for method,(label,_) in methods_spec.items():
        subset=[r for r in rows if r['method']==method]
        methods.append(dict(id=method,label=label,metrics=summarize_retrieval(subset),
                            categories={cat:summarize_retrieval([r for r in subset if r['category']==cat])
                                        for cat in ('exact','paraphrase','image','mixed')}))
    docs={d['id']:dict(drawing_number=d['drawing_number'],kind=d['kind']) for d in manifest['document_entries']}
    for case in cases:
        if case.get('query_image_id'):case['image_data_url']='data:image/png;base64,'+base64.b64encode(previews[case['query_image_id']]).decode()
    report=dict(status='measured',protocol=frozen,encoder_fingerprint=encoders.fingerprint,models=encoders.manifest,
                corpus_size=len(snapshot.revision_ids),cases=cases,documents=docs,methods=methods,
                rows=[r for r in rows if r['repeat']==1],model_load_ms=load_ms,index_ms=index_ms,
                snapshot_id=snapshot.id,graph_state=view.state,code_sha=os.getenv('GITHUB_SHA',subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()),
                runtime=dict(python=platform.python_version(),platform=platform.platform(),device='cpu'),
                raw_sha256=sha256((output/'runs.jsonl').read_bytes()).hexdigest(),
                limitations=['실제 산업 자료가 아닌 합성 도면150개·보류한6개 family의24개 질문입니다.',
                 '이미지 질문은 색인된 도면의 변형 crop입니다. 독립 촬영·외부 도면 일반화 검증이 아닙니다.',
                 '정답은 형상·치수 검색 관련도입니다. 재질·조립 적합성 판정 정확도와 다릅니다.',
                 '동일24개 질문을3회 반복했습니다. 독립72개 질문이나 유의한 모델 우위로 해석하지 않습니다.',
                 '모달리티가 없는 비교군은 빈 결과를 반환합니다. 전체 평균과 질문 유형별 표를 함께 봐야 합니다.',
                 '모델 로딩·색인은 지연 통계에서 분리했습니다. 무료 공개 체험은 기존 도번·키워드 엔진입니다.'])
    if ablation:
        report.update(reranker_models=reranker.manifest,reranker_load_ms=reranker_load_ms)
        report['limitations'][0]='기존 test/dev와 겹치지 않는 6개 family의 신규 질문입니다. 동일 합성 corpus이며 외부 독립 산업 데이터가 아닙니다.'
        report['limitations'].append('BGE는 텍스트만 읽으며 이미지-only 질문은 RRF 순위를 유지합니다. 조건 정렬은 질문에 명시된 조건과 원본 PDF 사실만 사용합니다.')
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    with gzip.GzipFile(filename=str(output/'runs.jsonl.gz'),mode='wb',mtime=0) as f:f.write((output/'runs.jsonl').read_bytes())
    print(json.dumps(dict(attempts=len(rows),errors=sum(r['status']!='ok' for r in rows),graph_state=view.state,
                          metrics={m['id']:m['metrics'] for m in methods}),ensure_ascii=False),flush=True)
    if any(r['status']!='ok' for r in rows) or view.state!='completed':raise RuntimeError('evaluation failures retained')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('var/corpus'));p.add_argument('--output',type=Path,required=True)
    p.add_argument('--ablation',action='store_true')
    a=p.parse_args();run(a.root,a.output,a.ablation)
