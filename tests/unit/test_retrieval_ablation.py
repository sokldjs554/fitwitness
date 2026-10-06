import json
from pathlib import Path


def test_new_reranking_questions_exclude_all_previous_test_families():
    from fitwitness.evaluation.retrieval import ablation_cases, ablation_protocol
    m=json.loads(Path('var/corpus/manifest.json').read_text())
    g=json.loads(Path('var/corpus/gold/labels.json').read_text())
    cases=ablation_cases(m,g)
    families={c['family_id'] for c in cases}
    assert len(cases)==36 and len(families)==6
    assert {c['category'] for c in cases}=={'exact','id_variant','id_typo','paraphrase','image','mixed'}
    typo=[c for c in cases if c['category']=='id_typo']
    assert all(c['text'] not in {d['drawing_number'] for d in m['document_entries']} for c in typo)
    assert families.isdisjoint(m['family_splits']['test'])
    assert families.isdisjoint(m['family_splits']['dev'])
    p=ablation_protocol(cases)
    assert p['candidate_pool']==20 and p['methods']==['hybrid','cross_encoder','constraints']
    assert p['repeats']==3
    assert all('requirements' not in c for c in cases)
