import importlib.util
import pytest

def funcs():
    assert importlib.util.find_spec('fitwitness.agents.tools'), 'typed tools missing'
    from fitwitness.agents.tools import ToolRequest,SearchPlan
    from fitwitness.agents.budget import BudgetTracker
    return ToolRequest,SearchPlan,BudgetTracker

def test_arbitrary_code_cannot_be_a_tool():
    T,_,_=funcs()
    with pytest.raises(ValueError):T(name='shell',arguments={'command':'rm -rf /'})

def test_unknown_argument_cannot_expand_scope():
    T,_,_=funcs()
    with pytest.raises(ValueError):T(name='query_dimensions',arguments={'revision_id':'a','fields':['width'],'tenant_id':'victim'})

def test_plan_is_bounded():
    T,P,_=funcs()
    with pytest.raises(ValueError):P(operations=[{'name':'search_keyword','arguments':{'query':'x'}}]*9)

def test_budget_never_allows_an_extra_call():
    _,_,B=funcs();from fitwitness.contracts import Budget
    budget=B(Budget(max_tool_calls=2))
    budget.tool();budget.tool()
    with pytest.raises(RuntimeError):budget.tool()

def test_region_outside_page_rejected():
    T,_,_=funcs()
    with pytest.raises(ValueError):T(name='read_region',arguments={'revision_id':'x','page':1,'bbox':[-1,0,1,1]})

def test_cost_reservation_blocks_call_before_spending():
    from decimal import Decimal
    from fitwitness.contracts import Budget
    _,_,B=funcs();b=B(Budget(max_cost_usd=Decimal('0.01')))
    with pytest.raises(RuntimeError,match='비용'):b.reserve(10000,1500,Decimal('5'),Decimal('15'))
    assert b.usage.model_calls==0

def test_model_usage_is_accounted_for_both_text_and_vision():
    from decimal import Decimal
    from fitwitness.contracts import Budget
    _,_,B=funcs();b=B(Budget())
    b.account({'input_tokens':1000,'output_tokens':100},Decimal('2'),Decimal('10'))
    assert b.usage.cost_usd==Decimal('0.003')
    assert b.usage.input_tokens==1000
