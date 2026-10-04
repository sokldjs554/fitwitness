"""Post-retrieval ordering, never a replacement for final evidence verification."""
import math
from functools import lru_cache
from pathlib import Path
import json
import os
from fitwitness.verification.conditions import verify
from fitwitness.retrieval.embeddings import verify_models


def order_constraints(candidates, requirements, snapshot_id):
    result=[]
    for candidate in candidates:
        decision=verify(requirements,candidate,snapshot_id)
        required={r.id for r in requirements if r.required}
        counts={v:sum(e.verdict==v and e.requirement_id in required for e in decision.evidence)
                for v in ('match','unknown','mismatch')}
        result.append(candidate.model_copy(update={'scores':{**candidate.scores,
            **{'condition_'+k:float(v) for k,v in counts.items()}}}))
    # Stable ties retain the original retrieval order. Unknowns are not removed.
    return sorted(result,key=lambda c:(-c.scores.get('exact',0),
        c.scores['condition_mismatch']>0,-c.scores['condition_match']))


def order_cross_encoder(candidates,query,passages,model):
    if not query.strip() or not candidates:
        return candidates
    values=model.score(query,[passages[c.revision_id] for c in candidates])
    if len(values)!=len(candidates) or not all(math.isfinite(float(x)) for x in values):
        raise ValueError('invalid cross-encoder scores')
    result=[c.model_copy(update={'scores':{**c.scores,'cross_encoder':float(v)}}) for c,v in zip(candidates,values)]
    return sorted(result,key=lambda c:(-c.scores.get('exact',0),-c.scores['cross_encoder']))


def reranker_lock():
    return json.loads(Path(__file__).with_name('reranker.json').read_text())


class CrossEncoder:
    def __init__(self):
        import torch
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        root=Path(os.getenv('FITWITNESS_MODEL_CACHE','var/models'))
        self.manifest=reranker_lock()
        verify_models(root,self.manifest)
        path=str(root/self.manifest['reranker']['folder'])
        torch.set_num_threads(int(os.getenv('FITWITNESS_TORCH_THREADS','2')))
        self.tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True)
        self.model=AutoModelForSequenceClassification.from_pretrained(path,local_files_only=True).eval().to('cpu')

    def score(self,query,passages):
        import torch
        scores=[]
        with torch.inference_mode():
            for start in range(0,len(passages),4):
                pairs=[[query,p] for p in passages[start:start+4]]
                inputs=self.tokenizer(pairs,padding=True,truncation=True,max_length=512,return_tensors='pt')
                scores.extend(self.model(**inputs).logits.view(-1).float().tolist())
        return scores


@lru_cache(maxsize=1)
def configured_reranker():
    if os.getenv('FITWITNESS_RERANKER','off')!='pretrained':
        raise ValueError('cross-encoder is not configured')
    return CrossEncoder()
