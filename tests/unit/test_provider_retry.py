from decimal import Decimal
from types import SimpleNamespace
import pytest
from fitwitness.agents.providers import ModelClient
from fitwitness.agents.budget import BudgetTracker
from fitwitness.agents.tools import SearchPlan
from fitwitness.contracts import Budget


class TemporaryError(Exception):
    status_code = 429


class Client:
    def __init__(self, errors): self.errors=list(errors)
    def with_structured_output(self, *a, **kw): return self
    def invoke(self, messages):
        if self.errors: raise self.errors.pop(0)
        return {'raw': SimpleNamespace(id='test-response',usage_metadata={'input_tokens':100,'output_tokens':30}), 'parsed':SearchPlan(stop=True)}


def model(errors, calls=8):
    m=ModelClient.__new__(ModelClient)
    m.provider='anthropic'; m.model_id='test'; m.budget=BudgetTracker(Budget(max_model_calls=calls,max_tokens=64000))
    m.input_rate=Decimal('1'); m.output_rate=Decimal('5'); m.client=Client(errors)
    m.guard=lambda:None; m.emit=lambda *args:None; m.wait=lambda seconds:None
    return m


def test_transient_retry_retains_uncertain_charge_and_accounts_success():
    m=model([TemporaryError('rate limit')])
    result, meta=m.plan({})
    assert result.stop
    assert m.budget.usage.model_calls==2
    assert m.budget.usage.reserved_cost_usd>0
    assert m.budget.usage.cost_usd==Decimal('.00025')
    assert meta['attempt']==2 and meta['latency_ms']>=0


def test_permanent_failure_is_not_retried():
    m=model([ValueError('bad request')])
    with pytest.raises(ValueError): m.plan({})
    assert m.budget.usage.model_calls==1


def test_retry_must_recheck_lease_and_budget():
    m=model([TemporaryError('busy')],calls=1)
    with pytest.raises(RuntimeError,match='모델 호출'): m.plan({})
    assert m.budget.usage.model_calls==1
    m=model([TemporaryError('busy')])
    def cancel(seconds): m.guard=lambda: (_ for _ in ()).throw(RuntimeError('cancelled'))
    m.wait=cancel
    with pytest.raises(RuntimeError,match='cancelled'): m.plan({})
    assert m.budget.usage.model_calls==1


def test_schema_failure_keeps_provider_response_and_usage_in_trace():
    m=model([]);events=[];m.emit=lambda kind,payload:events.append((kind,payload))
    raw=SimpleNamespace(id='lc_run--local',response_metadata={'id':'msg_provider','stop_reason':'tool_use'},usage_metadata={'input_tokens':100,'output_tokens':30},content=[{'type':'tool_use','input':{'stop_condition':'x'*501}}])
    m.client.invoke=lambda messages:{'raw':raw,'parsed':None,'parsing_error':ValueError('stop_condition too long')}
    with pytest.raises(ValueError): m.plan({})
    assert events and events[-1][0]=='model_schema_error'
    assert events[-1][1]['provider_response_id']=='msg_provider'
    assert events[-1][1]['usage']['input_tokens']==100
    assert 'stop_condition' in events[-1][1]['raw_output']


def test_an_output_that_fails_the_schema_goes_back_once_with_its_errors():
    m=model([]);events=[];seen=[];m.emit=lambda kind,payload:events.append((kind,payload))
    plan_sent={'operations':[{'name':'define_search_tool','arguments':{'channels':['keyword']}}]}
    bad=SimpleNamespace(id='lc1',response_metadata={'id':'msg_1','stop_reason':'tool_use'},usage_metadata={'input_tokens':100,'output_tokens':30},
                        content=[{'type':'tool_use','input':plan_sent}],tool_calls=[{'name':'SearchPlan','args':plan_sent,'id':'toolu_1'}])
    good=SimpleNamespace(id='lc2',response_metadata={'id':'msg_2'},usage_metadata={'input_tokens':150,'output_tokens':40})
    answers=[{'raw':bad,'parsed':None,'parsing_error':ValueError("channels.0 Input should be 'exact', 'bm25' or 'semantic'")},
             {'raw':good,'parsed':SearchPlan(stop=True),'parsing_error':None}]
    def invoke(messages): seen.append(list(messages)); return answers.pop(0)
    m.client.invoke=invoke
    plan,meta=m.plan({})
    assert plan.stop and meta['repair']==1 and meta['provider_response_id']=='msg_2'
    assert m.budget.usage.model_calls==2 and m.budget.usage.input_tokens==250 and m.budget.usage.reserved_cost_usd==0
    assert [k for k,_ in events]==['model_schema_error'] and events[0][1]['repaired_next'] is True
    note=seen[1][-1].content  # the second call carries the first two messages plus the errors and the rejected plan
    assert len(seen[1])==len(seen[0])+1 and "'exact', 'bm25' or 'semantic'" in note and 'keyword' in note


def test_a_second_output_that_fails_the_schema_ends_the_call():
    m=model([]);events=[];m.emit=lambda kind,payload:events.append((kind,payload))
    raw=SimpleNamespace(id='lc',response_metadata={'id':'msg','stop_reason':'tool_use'},usage_metadata={'input_tokens':100,'output_tokens':30},content=[])
    m.client.invoke=lambda messages:{'raw':raw,'parsed':None,'parsing_error':ValueError('still wrong')}
    with pytest.raises(ValueError,match='schema'): m.plan({})
    assert [k for k,_ in events]==['model_schema_error','model_schema_error'] and m.budget.usage.model_calls==2
    assert events[1][1]['repaired_next'] is False
