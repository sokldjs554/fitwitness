"""Run the actual LangGraph with an in-memory checkpoint and explicit DB boundary fakes."""
import pytest
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from langgraph.checkpoint.memory import InMemorySaver
from fitwitness.contracts import RunRequest, SearchRequest, Usage, TenantScope
from fitwitness.agents.tools import SearchPlan
from test_agent_evidence import candidate


def test_graph_does_not_approve_uninspected_search_results(monkeypatch):
    import fitwitness.agents.graph as mod
    req=RunRequest(search=SearchRequest(text='SUS304'),provider='anthropic',model_id='test')
    raw={'request':req.model_dump(mode='json'),'usage':Usage().model_dump(mode='json'),'created_at':datetime.now(timezone.utc),'snapshot_id':'snap'}
    output=[]; roles=[]
    class Jobs:
        def __init__(self,repo): pass
        def raw(self,*args): return raw
        def claim(self,*args,**kwargs): return 'lease'
        def owns_lease(self,*args): return True
        def event(self,*args,**kwargs): pass
        def finalize(self,*args): output.extend(args[3])
        def fail(self,*args): pass
        def save_usage(self,*args): pass
    class Model:
        def plan(self,context,role='planner'):
            roles.append(role)
            return SearchPlan(stop=True), {'role':role}
    saver=InMemorySaver(); saver.setup=lambda:None
    monkeypatch.setattr(mod,'Jobs',Jobs)
    monkeypatch.setattr(mod,'checkpointer',lambda dsn:nullcontext(saver))
    monkeypatch.setattr(mod,'search',lambda *args:[candidate()])
    monkeypatch.setattr(mod,'create_model',lambda *args:Model())
    repo=SimpleNamespace(dsn='unused',snapshot=lambda s:SimpleNamespace(id='snap'),corpus=lambda s:(SimpleNamespace(id='snap'),[],{}))
    mod._execute_run(repo,TenantScope(tenant_id='t',user_id='u'),'run')
    assert [d.verdict for d in output]==['unknown']
    assert roles==['planner','challenger']


@pytest.mark.parametrize("limits", [{"max_model_calls":1}, {"max_tool_calls":2}, {"max_cost_usd":"0.00061"}])
def test_budget_stop_keeps_verified_result_without_extra_challenger(monkeypatch, limits):
    import fitwitness.agents.graph as mod
    from fitwitness.contracts import Budget
    from fitwitness.agents.tools import ToolRequest
    from decimal import Decimal
    req=RunRequest(search=SearchRequest(text='SUS304'),provider='anthropic',model_id='test',budget=Budget(**limits))
    raw={'request':req.model_dump(mode='json'),'usage':Usage().model_dump(mode='json'),'created_at':datetime.now(timezone.utc),'snapshot_id':'snap'}
    output=[]; roles=[]
    class Jobs:
        def __init__(self,repo): pass
        def raw(self,*args): return raw
        def claim(self,*args,**kwargs): return 'lease'
        def owns_lease(self,*args): return True
        def event(self,*args,**kwargs): pass
        def finalize(self,*args): output.extend(args[3])
        def fail(self,*args): pass
        def save_usage(self,*args): pass
    class Model:
        def __init__(self,budget): self.budget=budget
        def plan(self,context,role='planner'):
            roles.append(role)
            self.budget.reserve(100,100,Decimal('1'),Decimal('5'))
            self.budget.account({'input_tokens':10,'output_tokens':10},Decimal('1'),Decimal('5'))
            return SearchPlan(operations=[ToolRequest(name='query_dimensions',arguments={'revision_id':'r'})]), {'role':role}
    saver=InMemorySaver(); saver.setup=lambda:None
    monkeypatch.setattr(mod,'Jobs',Jobs)
    monkeypatch.setattr(mod,'checkpointer',lambda dsn:nullcontext(saver))
    monkeypatch.setattr(mod,'search',lambda *args:[candidate()])
    monkeypatch.setattr(mod,'create_model',lambda p,m,b:Model(b))
    repo=SimpleNamespace(dsn='unused',snapshot=lambda s:SimpleNamespace(id='snap',revision_ids=['r']),corpus=lambda s:(SimpleNamespace(id='snap',revision_ids=['r']),[],{}),load_facts=lambda *args:candidate().facts)
    mod._execute_run(repo,TenantScope(tenant_id='t',user_id='u'),'run')
    assert [d.verdict for d in output]==['match']
    assert roles[0]=='planner'
    assert len(output)==1
