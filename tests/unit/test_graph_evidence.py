"""Run the actual LangGraph with an in-memory checkpoint and explicit DB boundary fakes."""
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
    monkeypatch.setattr(mod.PostgresSaver,'from_conn_string',lambda dsn:nullcontext(saver))
    monkeypatch.setattr(mod,'search',lambda *args:[candidate()])
    monkeypatch.setattr(mod,'create_model',lambda *args:Model())
    repo=SimpleNamespace(dsn='unused',snapshot=lambda s:SimpleNamespace(id='snap'))
    mod._execute_run(repo,TenantScope(tenant_id='t',user_id='u'),'run')
    assert [d.verdict for d in output]==['unknown']
    assert roles==['planner','challenger']
