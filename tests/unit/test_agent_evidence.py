"""Regression: only inspected evidence may justify a paid agent's decision."""
import importlib.util
from fitwitness.contracts import Candidate, Fact, SourceRef, Requirement
from fitwitness.agents.tools import ToolRequest
from fitwitness.verification.conditions import verify


def session():
    assert importlib.util.find_spec('fitwitness.agents.evidence'), 'evidence session missing'
    from fitwitness.agents.evidence import EvidenceSession
    return EvidenceSession


def candidate():
    return Candidate(revision_id='r', scores={'bm25': 1}, facts=[Fact(
        id='f', field='material', value='SUS304', source=SourceRef(
            revision_id='r', source_hash='a'*64, page=1, bbox=(0,0,.5,.5)))])


def test_search_does_not_supply_inspection_evidence():
    s=session()([candidate()], available={'search_keyword','query_dimensions'})
    req=Requirement(id='q',field='material',operator='eq',value='SUS304',source_text='SUS304')
    assert verify([req], s.candidates[0], 'snapshot').verdict=='unknown'
    s.observe(ToolRequest(name='query_dimensions',arguments={'revision_id':'r'}),
              [candidate().facts[0].model_dump(mode='json')])
    assert verify([req], s.candidates[0], 'snapshot').verdict=='match'


def test_search_added_candidates_also_need_inspection():
    s=session()([],available={'search_keyword'})
    s.observe(ToolRequest(name='search_keyword',arguments={'query':'bracket'}),[candidate().model_dump(mode='json')])
    assert s.candidates[0].facts==[]


def test_foreign_observation_cannot_be_attached_to_candidate():
    import pytest
    s=session()([candidate()],available={'query_dimensions'})
    f=candidate().facts[0].model_copy(deep=True); f.source.revision_id='foreign'
    with pytest.raises(ValueError):
        s.observe(ToolRequest(name='query_dimensions',arguments={'revision_id':'r'}),[f.model_dump(mode='json')])
    assert s.candidates[0].facts==[]


def test_context_exposes_only_available_tools_without_source_duplication():
    import json
    s=session()([candidate()],available={'search_keyword','query_dimensions'})
    c=s.context('bracket',[],[])
    assert set(c['tools'])=={'search_keyword','query_dimensions'}
    assert 'source_hash' not in json.dumps(c)
    assert len(json.dumps(c).encode()) < 4000


def test_empty_candidates_retry_and_explicit_stop_honored():
    s=session()([],available={'search_keyword'})
    assert s.needs_more([], iterations=1, stop=False)
    assert not s.needs_more([], iterations=1, stop=True)
    assert not s.needs_more([], iterations=3, stop=False)


def test_context_distinguishes_absent_material_from_uninspected_field():
    s=session()([candidate()],available={'query_dimensions'})
    op={'name':'query_dimensions','arguments':{'revision_id':'r','fields':['material']}}
    context=s.context('SUS304',[{'field':'material'}],[{'tool':op,'result':[]}])
    assert context['candidates'][0]['missing_fields']==['material']
    assert context['candidates'][0]['examined_fields']==['material']


def test_query_arguments_are_canonical_for_repeat_detection():
    cls=session()
    assert hasattr(cls,'tool_key')
    a=ToolRequest(name='query_dimensions',arguments={'revision_id':'r','fields':['material','width']})
    b=ToolRequest(name='query_dimensions',arguments={'fields':['width','material'],'revision_id':'r'})
    assert cls.tool_key(a)==cls.tool_key(b)
