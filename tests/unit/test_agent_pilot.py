import importlib.util
from decimal import Decimal


def test_paid_pilot_allocations_never_exceed_approved_total():
    assert importlib.util.find_spec('fitwitness.evaluation.agent_pilot'), 'agent pilot missing'
    from fitwitness.evaluation.agent_pilot import PROTOCOL,score
    assert Decimal(PROTOCOL['pdf_cap_usd'])+sum(Decimal(x['cap_usd']) for x in PROTOCOL['runs'])<=1
    assert len(PROTOCOL['runs'])==6
    # Missing candidates and unknowns count as errors, not dropped rows.
    result=score({'a':'match','b':'mismatch'}, {'a':'match'})
    assert result['correct']==1 and result['total']==2 and result['accuracy']==.5


def test_every_pilot_request_including_rules_is_valid_before_execution():
    from fitwitness.evaluation import agent_pilot
    assert hasattr(agent_pilot,'build_request'), 'pilot request preflight missing'
    configs=[{'mode':'fixed','repeat':0,'cap_usd':'0.01','provider':'rules'},*agent_pilot.PROTOCOL['runs']]
    for config in configs:
        request=agent_pilot.build_request(config)
        assert isinstance(request.model_id,str)
        assert request.budget.max_cost_usd==Decimal(config['cap_usd'])
