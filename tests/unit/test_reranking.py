import pytest
from fitwitness.contracts import Candidate, Fact, Interval, Requirement, SearchRequest, SourceRef


def candidate(rid, width=None, certainty='verified'):
    facts=[] if width is None else [Fact(field='width',value=Interval(low=width,high=width),unit='mm',certainty=certainty,
        source=SourceRef(revision_id=rid,source_hash='a'*64,page=1,bbox=(0,0,.5,.5)))]
    return Candidate(revision_id=rid,scores={'rrf':.02},facts=facts)


def test_constraint_order_preserves_unknown_and_does_not_invent_candidates():
    from fitwitness.retrieval.reranking import order_constraints
    req=[Requirement(field='width',value=Interval(low=4,high=4),unit='cm')]
    source=[candidate('bad',42),candidate('absent'),candidate('good',40),candidate('uncertain',40,'uncertain')]
    found=order_constraints(source,req,'snapshot')
    assert [c.revision_id for c in found]==['good','absent','uncertain','bad']
    assert source[0].scores=={'rrf':.02}
    assert found[1].scores['condition_unknown']==1


def test_constraint_order_never_displaces_an_explicit_drawing_number():
    from fitwitness.retrieval.reranking import order_constraints
    exact=candidate('exact',42);exact.scores['exact']=1
    req=[Requirement(field='width',value=Interval(low=40,high=40),unit='mm')]
    assert order_constraints([exact,candidate('good',40)],req,'s')[0].revision_id=='exact'


def test_cross_encoder_keeps_mapping_and_records_real_scores():
    from fitwitness.retrieval.reranking import order_cross_encoder
    class Scorer:
        def score(self,query,passages):
            assert query=='폭 40mm' and passages==['A','B']
            return [-2.,3.]
    result=order_cross_encoder([candidate('a'),candidate('b')],'폭 40mm',{'a':'A','b':'B'},Scorer())
    assert [c.revision_id for c in result]==['b','a']
    assert result[0].scores=={'rrf':.02,'cross_encoder':3.}


@pytest.mark.parametrize('values',[[1.],[float('nan'),1.]])
def test_cross_encoder_invalid_scores_fail_closed(values):
    from fitwitness.retrieval.reranking import order_cross_encoder
    class Scorer:
        def score(self,*args):return values
    with pytest.raises(ValueError):order_cross_encoder([candidate('a'),candidate('b')],'q',{'a':'A','b':'B'},Scorer())


def test_image_only_does_not_call_text_reranker():
    from fitwitness.retrieval.reranking import order_cross_encoder
    class Scorer:
        def score(self,*args):raise AssertionError('no text')
    source=[candidate('a')]
    assert order_cross_encoder(source,'',{'a':'A'},Scorer())==source


def test_ranking_selection_is_explicit_and_validated():
    assert SearchRequest().ranking=='rrf'
    assert SearchRequest(ranking='constraints').ranking=='constraints'
    with pytest.raises(ValueError):SearchRequest(ranking='typo')


def test_reranker_lock_is_revision_and_content_pinned():
    from fitwitness.retrieval.reranking import reranker_lock
    model=reranker_lock()['reranker']
    assert len(model['revision'])==40 and model['license']=='Apache-2.0'
    assert len(model['files']['model.safetensors'])==64


def test_pipeline_reranks_before_cutoff_and_rechecks_snapshot():
    from fitwitness.retrieval.pipeline import search
    from test_indexing import Repo,scope
    class Many(Repo):
        def __init__(self):
            super().__init__()
            self.revisions=[self.rev.model_copy(update={'id':str(i),'drawing_number':f'D-{i}'}) for i in range(8)]
        def list_revisions(self,*args):return self.revisions
        def snapshot(self,*args):
            return super().snapshot().model_copy(update={'revision_ids':[r.id for r in self.revisions]})
        def load_facts_many(self,scope,ids):return {rid:candidate(rid,40 if rid=='7' else 42).facts for rid in ids}
    repo=Many()
    req=SearchRequest(text='plate 폭 40mm',top_k=1,ranking='constraints')
    result=search(scope,req,repo.snapshot(),repo,channels={'bm25'})
    assert result[0].revision_id=='7'
    class ChangingScorer:
        def score(self,q,passages):
            repo.snapshot=lambda *a:super(Many,repo).snapshot().model_copy(update={'id':'changed'})
            return [1.]*len(passages)
    with pytest.raises(ValueError,match='snapshot'):
        search(scope,req.model_copy(update={'ranking':'cross_encoder'}),repo.snapshot(),repo,channels={'bm25'},reranker=ChangingScorer())
