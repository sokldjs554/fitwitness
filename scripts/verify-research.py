"""Offline audit of frozen ranks, images, field scores and paid usage."""
import base64,gzip,hashlib,json,math
from decimal import Decimal
from pathlib import Path
from statistics import mean
E=Path(__file__).resolve().parents[1]/'docs/evaluation'
def read(p):return json.loads(p.read_text())
def digest(b):return hashlib.sha256(b).hexdigest()
r=read(E/'reranking-37206750470/report.json');p=read(E/'reranking-37206750470/protocol.json')
assert r['protocol']==p
raw=gzip.decompress((E/'reranking-37206750470/runs.jsonl.gz').read_bytes());rows=[json.loads(x) for x in raw.splitlines()]
assert digest(raw)==r['raw_sha256'] and len(rows)==216 and all(x['status']=='ok' for x in rows)
assert len({(x['case_id'],x['method'],x['repeat']) for x in rows})==216
assert r['rows']==[x for x in rows if x['repeat']==1]
old=read(E/'retrieval-37198685994/protocol.json')
assert p['source_hashes']==old['source_hashes'] and p['source_image_hashes']==old['source_image_hashes']
assert not {c['family_id'] for c in p['cases']}&{c['family_id'] for c in old['cases']}
cases={c['id']:c for c in p['cases']}
for c in r['cases']:
 if c.get('image_data_url'):assert digest(base64.b64decode(c['image_data_url'].split(',')[1]))==p['query_image_hashes'][c['id']]
for x in rows:
 q=cases[x['case_id']]['relevance'];ids=[c['revision_id'] for c in x['ranked']]
 assert len(ids)==len(set(ids))
 recall=sum(i in q for i in ids[:5])/len(q)
 dcg=sum((2**q.get(i,0)-1)/math.log2(j+2) for j,i in enumerate(ids[:10]))
 ideal=sum((2**v-1)/math.log2(j+2) for j,v in enumerate(sorted(q.values(),reverse=True)[:10]))
 assert math.isclose(recall,x['recall_at_5']) and math.isclose(dcg/ideal,x['ndcg_at_10'])
for method in r['methods']:
 for category,m in [('all',method['metrics']),*method['categories'].items()]:
  selected=[x for x in rows if x['method']==method['id'] and (category=='all' or x['category']==category)]
  assert m['attempts']==m['completed']==len(selected)
  for k in ['recall_at_5','ndcg_at_10']:assert math.isclose(m[k],mean(x[k] for x in selected))
  values=sorted(x['latency_ms'] for x in selected)
  for suffix,quantile in [('50',.5),('95',.95)]:
   k=(len(values)-1)*quantile;lo=int(k);hi=min(lo+1,len(values)-1)
   assert math.isclose(m['latency_p'+suffix+'_ms'],values[lo]+(values[hi]-values[lo])*(k-lo))
v=E/'vision-37207929539';report=read(v/'report.json');vp=read(v/'protocol.json');g=read(v/'graph.json')
vraw=(v/'runs.jsonl').read_bytes();vr=[json.loads(x) for x in vraw.splitlines()]
assert report['protocol']==vp and report['rows']==vr and report['graph']==g
assert digest(vraw)==report['raw_sha256'] and len(vr)==18 and len({(x['case_id'],x['repeat']) for x in vr})==18
assert not {c['family_id'] for c in vp['cases']}&{c['family_id'] for c in p['cases']}
vc={c['id']:c for c in vp['cases']}
for c in vp['cases']:
 assert digest(base64.b64decode(c['image_data_url'].split(',')[1]))==c['image_hash']
 assert c['source_image_hash']==old['source_image_hashes'][c['revision_id']]
for x in vr:
 assert x['usage']==read(v/f"budget-{x['case_id']}-{x['repeat']}.json")
 for field,truth in vc[x['case_id']]['expected'].items():
  if x['status']=='error':correct=False
  else:
   values=[a for a in x['reading']['annotations'] if a['field']==field and a['value'] is not None]
   if truth is None:correct=not values
   elif field=='material':correct=bool(values) and all(a['value'].strip().upper()==truth for a in values)
   else:
    try:correct=bool(values) and all(Decimal(a['value'].strip())*{'mm':Decimal(1),'cm':Decimal(10),'m':Decimal(1000),'in':Decimal('25.4')}[a['unit']]==Decimal(truth) for a in values)
    except Exception:correct=False
  assert correct==x['fields'][field]
 payload=x['metadata'] or next(y['payload'] for y in x['events'] if y['kind']=='model_schema_error')
 assert payload['provider_response_id'].startswith('msg_')
known=sum(Decimal(x['usage']['cost_usd']) for x in vr)+Decimal(g['run']['usage']['cost_usd'])
reserved=sum(Decimal(x['usage']['reserved_cost_usd']) for x in vr)+Decimal(g['run']['usage']['reserved_cost_usd'])
assert known==Decimal(report['metrics']['known_cost_usd']) and reserved==Decimal(report['metrics']['unresolved_reserved_usd'])
assert known+reserved<=Decimal('.55') and known+reserved+Decimal('.187841')<=1
assert report['metrics']['field_accuracy']==mean(y for x in vr for y in x['fields'].values())
assert g['visual_calls']==1 and g['run']['state']=='completed' and all(x['verdict']=='unknown' for x in g['run']['decisions'])
assert (E/'reranking.json').read_bytes()==(E/'reranking-37206750470/report.json').read_bytes()
assert (E/'vision.json').read_bytes()==(v/'report.json').read_bytes()
print(json.dumps({'reranking_rows':216,'vision_attempts':18,'vision_correct_fields':sum(y for x in vr for y in x['fields'].values()),'vision_cost_usd':str(known),'aggregate_cost_usd':str(known+Decimal('.187841')),'unresolved_usd':str(reserved),'graph':'completed, one visual call, unknown verdict','source_hashes':'match frozen archive'},indent=2))
