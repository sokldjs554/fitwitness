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
