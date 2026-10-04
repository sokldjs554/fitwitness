"""Measured reports must preserve failures, provenance, and independent denominators."""
import importlib.util
import pytest


def api():
    assert importlib.util.find_spec('fitwitness.evaluation.metrics'), 'evaluation metrics missing'
    from fitwitness.evaluation.metrics import summarize, parse_prediction, model_payload
    return summarize, parse_prediction, model_payload


def row(expected='match', predicted='match', status='ok', family='a'):
    return dict(expected=expected, predicted=predicted, status=status, family_id=family,
                latency_ms=100, input_tokens=20, output_tokens=5,
                citations=['F0'], valid_citations=['F0'], cost_usd=None)


def test_failed_calls_stay_in_accuracy_denominator():
    summarize, _, _ = api()
    m=summarize([row(),row(predicted=None,status='error',family='b')])
    assert m['accuracy']==0.5 and m['completed']==1 and m['attempts']==2
    assert m['cost_usd'] is None


def test_false_acceptance_uses_nonmatching_gold_denominator():
    summarize, _, _ = api()
    m=summarize([row(),row('mismatch','match'),row('unknown','unknown',family='b')])
    assert m['false_acceptance_rate']==0.5
    assert m['false_acceptances']==1
    assert m['accuracy_ci95'][0]<=m['accuracy']<=m['accuracy_ci95'][1]


def test_schema_and_citation_errors_are_not_silently_repaired():
    _, parse, _=api()
    assert parse('{"verdict":"unknown","citations":[]}', {'F0'})['verdict']=='unknown'
    with pytest.raises(ValueError): parse('{"verdict":"match","citations":["invented"]}', {'F0'})
    with pytest.raises(ValueError): parse('maybe match', {'F0'})
    with pytest.raises(ValueError): parse('{"verdict":"match","citations":[]}', {'F0'})


def test_model_input_never_contains_gold_or_prediction():
    _, _, payload=api()
    case=dict(query='40mm',requirements=[],facts=[],expected='mismatch',family_id='test',case_id='q')
    assert set(payload(case))=={'query','requirements','facts'}
    assert 'expected' not in str(payload(case))


def test_no_fake_numbers_for_missing_runs():
    summarize, _, _=api()
    m=summarize([])
    assert m['attempts']==0 and m['accuracy'] is None and m['latency_p95_ms'] is None


def test_invalid_model_citations_remain_in_metric_denominator():
    from fitwitness.evaluation.runner import measure_call
    class Model:
        def predict(self, payload, seed):
            return '{"verdict":"match","citations":["invented"]}', dict(input_tokens=20,output_tokens=5,cost_usd=0.0)
    result=row();result.update(status='error',predicted=None,citations=[])
    measure_call(Model(),{},1701,result)
    assert result['status']=='error' and result['predicted'] is None
    assert result['citations']==['invented']
    assert api()[0]([row(),result])['citation_validity']==0.5


def test_api_schema_failure_retains_usage_and_raw_response():
    from decimal import Decimal
    from langchain_core.messages import AIMessage
    from fitwitness.evaluation.runner import APIModel, measure_call
    raw=AIMessage(content='not valid JSON',id='request-123',usage_metadata=dict(input_tokens=30,output_tokens=7,total_tokens=37))
    class Client:
        def with_structured_output(self,*args,**kwargs): return self
        def invoke(self,*args,**kwargs): return {'raw':raw,'parsed':None,'parsing_error':ValueError('bad schema')}
    model=APIModel.__new__(APIModel)
    model.input_rate=model.output_rate=Decimal('1')
    model.remaining=Decimal('1');model.client=Client()
    result=row();result.update(status='error',predicted=None,citations=[],input_tokens=0,output_tokens=0)
    measure_call(model,{},1701,result)
    assert result['status']=='error'
    assert result['input_tokens']==30 and result['output_tokens']==7
    assert result['cost_usd']==pytest.approx(0.000037)
    assert result['request_id']=='request-123'
    assert 'not valid JSON' in result['raw_output']
