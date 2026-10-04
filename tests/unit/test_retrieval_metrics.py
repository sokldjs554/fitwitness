import pytest

def test_retrieval_metrics_known_order_and_duplicates():
    from fitwitness.evaluation.retrieval import score_ranking
    s=score_ranking(['b','b','a','c'],{'a':2,'c':1},k=2)
    assert s['recall']==.5
    assert s['ndcg']==pytest.approx((3/1.584962500721156)/(3+1/1.584962500721156))
    assert score_ranking([],{'a':1})=={'recall':0.,'ndcg':0.}
    assert score_ranking(['x'],{})=={'recall':None,'ndcg':None}


def test_frozen_queries_exclude_train_and_hide_labels_from_request():
    from fitwitness.evaluation.retrieval import make_cases, protocol
    from pathlib import Path
    import json
    manifest=json.loads(Path('var/corpus/manifest.json').read_text())
    cases=make_cases(manifest, json.loads(Path('var/corpus/gold/labels.json').read_text()))
    assert len(cases)==24
    assert {c['family_id'] for c in cases}==set(manifest['family_splits']['test'])
    assert {c['category'] for c in cases}=={'exact','paraphrase','image','mixed'}
    assert all(c['relevance'] for c in cases)
    p=protocol(cases)
    assert p['repeats']==3 and p['rrf_k']==60 and p['top_k']==10
    assert len(p['dataset_hash'])==64


def test_protocol_is_frozen_before_runtime_case_annotations():
    from fitwitness.evaluation.retrieval import protocol
    cases=[{'id':'q','relevance':{'r':1}}]
    frozen=protocol(cases)
    cases[0]['query_image_id']='runtime-image'
    assert 'query_image_id' not in frozen['cases'][0]


def test_text_query_does_not_require_an_unstated_hole_spacing():
    from fitwitness.evaluation.retrieval import make_cases
    from pathlib import Path
    import json
    m=json.loads(Path('var/corpus/manifest.json').read_text())
    cases=make_cases(m,json.loads(Path('var/corpus/gold/labels.json').read_text()))
    for family in ('FW-F012','FW-F029'):
        alternate=next(d for d in m['document_entries'] if d['family_id']==family and d['drawing_number'].endswith('-1'))
        text=next(c for c in cases if c['family_id']==family and c['category']=='paraphrase')
        image=next(c for c in cases if c['family_id']==family and c['category']=='image')
        assert alternate['id'] in text['relevance']
        assert alternate['id'] not in image['relevance']
