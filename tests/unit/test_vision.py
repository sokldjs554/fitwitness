from io import BytesIO
from types import SimpleNamespace
import pytest
from PIL import Image
from test_provider_retry import model, TemporaryError
from fitwitness.contracts import Candidate, Interval, Requirement
from fitwitness.verification.conditions import verify


def png():
    b=BytesIO();Image.new('RGB',(300,200),'white').save(b,format='PNG');return b.getvalue()


def test_vision_structured_response_tracks_provider_id_usage_and_uncertainty():
    from fitwitness.ingest.vision import ImageReading, image_facts
    m=model([])
    reading=ImageReading(annotations=[{'field':'width','value':'40','unit':'mm','bbox':(.1,.2,.5,.3),'visible_text':'40 mm'}])
    raw=SimpleNamespace(id='lc_local',response_metadata={'id':'msg_vision'},usage_metadata={'input_tokens':1200,'output_tokens':100},content=[])
    m.client.invoke=lambda messages:{'parsed':reading,'raw':raw}
    result,meta=m.read_image(png(),'Read width.')
    assert result==reading and meta['provider_response_id']=='msg_vision' and meta['role']=='vision'
    assert m.budget.usage.model_calls==1 and m.budget.usage.reserved_cost_usd==0
    facts=image_facts(reading,'r','a'*64,(.2,.2,.8,.8))
    assert facts[0].certainty=='uncertain' and facts[0].source.bbox==pytest.approx((.26,.32,.5,.38))
    req=Requirement(field='width',value=Interval(low=40,high=40),unit='mm')
    assert verify([req],Candidate(revision_id='r',facts=facts),'s').verdict=='unknown'


def test_vision_rechecks_guard_before_any_charge_or_remote_call():
    m=model([])
    m.guard=lambda:(_ for _ in ()).throw(RuntimeError('lease lost'))
    with pytest.raises(RuntimeError,match='lease lost'):m.read_image(png(),'Read width.')
    assert m.budget.usage.model_calls==0


def test_invalid_image_is_rejected_before_charge():
    m=model([])
    with pytest.raises(ValueError):m.read_image(b'not image','read')
    assert m.budget.usage.model_calls==0


def test_unknown_or_invalid_vision_numbers_are_not_promoted_to_facts():
    from fitwitness.ingest.vision import ImageReading,image_facts
    reading=ImageReading(annotations=[{'field':'width','value':'4O','unit':'mm','bbox':(0,0,.5,.5),'visible_text':'4O mm'},
        {'field':'material','value':None,'unit':None,'bbox':(0,0,.5,.5),'visible_text':'unreadable'}])
    assert image_facts(reading,'r','a'*64)==[]


def test_vision_tool_is_only_available_with_explicit_reader():
    from fitwitness.agents.tools import EvidenceTools
    from fitwitness.agents.budget import BudgetTracker
    from fitwitness.contracts import Budget
    t=EvidenceTools(None,None,None,BudgetTracker(Budget()))
    assert 'read_image_region' not in t.available
    assert 'read_image_region' not in {x.name for x in t.langchain_tools()}
    t=EvidenceTools(None,None,None,BudgetTracker(Budget()),vision=object())
    assert 'read_image_region' in t.available


def test_missing_pdf_fields_can_receive_one_visual_observation_before_exhaustion():
    from fitwitness.agents.evidence import EvidenceSession
    session=EvidenceSession([Candidate(revision_id='r')],{'query_dimensions','read_image_region'})
    req=[{'field':'width'}]
    observations=[{'tool':{'name':'query_dimensions','arguments':{'revision_id':'r','fields':['width']}},'result':[]}]
    assert not session.exhausted(req,observations)
    observations.append({'tool':{'name':'read_image_region','arguments':{'revision_id':'r','fields':['width'],'bbox':[0,0,1,1]}},'result':[]})
    assert session.exhausted(req,observations)


def test_later_verified_pdf_fact_overrides_uncertain_visual_observation():
    from fitwitness.ingest.vision import ImageReading,image_facts
    reading=ImageReading(annotations=[{'field':'width','value':'40','unit':'mm','bbox':(0,0,.5,.5),'visible_text':'40'}])
    fact=image_facts(reading,'r','a'*64)[0]
    verified=fact.model_copy(update={'certainty':'verified','method':'vector_pdf'})
    req=Requirement(field='width',value=Interval(low=40,high=40),unit='mm')
    assert verify([req],Candidate(revision_id='r',facts=[fact,verified]),'s').verdict=='match'


def test_visual_field_metric_scores_missing_labels_and_unit_conversion():
    from fitwitness.evaluation.vision import field_scores
    from fitwitness.ingest.vision import ImageReading
    a=ImageReading(annotations=[{'field':'width','value':'4','unit':'cm','bbox':(0,0,.5,.5),'visible_text':'4 cm'}])
    assert field_scores(a,{'width':'40','material':None})=={'width':True,'material':True}
    assert field_scores(a,{'width':None,'material':None})=={'width':False,'material':True}


@pytest.mark.parametrize('value',['1e1000000','NaN','Infinity'])
def test_extreme_visual_numbers_are_scored_wrong_without_losing_raw_reading(value):
    from fitwitness.evaluation.vision import field_scores
    from fitwitness.ingest.vision import ImageReading
    reading=ImageReading(annotations=[dict(field='width',value=value,unit='mm',visible_text='?',bbox=(0,0,1,1))])
    assert field_scores(reading,{'width':'40','material':None})=={'width':False,'material':True}
    assert reading.annotations[0].value==value
