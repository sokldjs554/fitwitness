"""LangGraph executes evidence steps with durable PostgreSQL checkpoints."""
from typing import TypedDict
import json,os,threading,time
from langgraph.graph import StateGraph,START,END
from langgraph.checkpoint.postgres import PostgresSaver
from fitwitness.contracts import Candidate,Decision,Fact,Requirement,RunRequest,Usage
from fitwitness.retrieval.pipeline import extract_requirements,search
from fitwitness.verification.conditions import verify
from fitwitness.agents.tools import EvidenceTools,ToolRequest,SCHEMAS
from fitwitness.agents.budget import BudgetTracker
from fitwitness.agents.providers import create_model
from fitwitness.runtime.jobs import Jobs

class State(TypedDict,total=False):
    requirements:list[dict]
    candidates:list[dict]
    decisions:list[dict]
    iterations:int
    observations:list[dict]
    usage:dict


def execute_run(repo,scope,run_id,*,encoders=None,fault_after_retrieval=False):
    jobs=Jobs(repo);raw=jobs.raw(scope,run_id)
    if not raw:return
    token=jobs.claim(scope,run_id)
    if not token:return
    request=RunRequest.model_validate(raw['request']);snapshot=repo.snapshot(scope);budget=BudgetTracker(request.budget)
    stop=threading.Event()
    def heartbeat():
        while not stop.wait(10):
            if not jobs.heartbeat(scope,run_id,token):return
    thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
    try:
        if snapshot.id!=raw['snapshot_id']:
            jobs.finalize(scope,run_id,raw['snapshot_id'],[],token);return
        tools=EvidenceTools(scope,snapshot,repo,budget,encoders)
        model=create_model(request.provider,request.model_id,budget) if request.provider!='rules' else None
        def guard():
            budget.check()
            if jobs.get(scope,run_id).state!='running':raise RuntimeError('실행이 취소되거나 변경되었습니다')
        def intent(state):
            guard();reqs=request.search.requirements or extract_requirements(request.search.text)
            jobs.event(scope,run_id,'intent',{'requirements':[r.model_dump(mode='json') for r in reqs]})
            return {'requirements':[r.model_dump(mode='json') for r in reqs],'iterations':0,'observations':[]}
        def retrieve(state):
            guard();budget.tool();channels={'exact','bm25'} | ({'semantic','image'} if encoders else set())
            candidates=search(scope,request.search,snapshot,repo,encoders,channels)
            jobs.event(scope,run_id,'retrieved',{'candidates':[{'revision_id':c.revision_id,'scores':c.scores} for c in candidates]})
            return {'candidates':[c.model_dump(mode='json') for c in candidates],'usage':budget.usage.model_dump(mode='json')}
        def inspect(state):
            guard()
            # This node follows a persisted retrieval checkpoint. Fault injection is scoped to this worker process.
            if fault_after_retrieval and jobs.mark_fault_consumed(scope,run_id):
                jobs.event(scope,run_id,'interrupted',{'reason':'체험용 worker 종료; 검색 checkpoint 보존'});os._exit(86)
            candidates=[Candidate.model_validate(c) for c in state.get('candidates',[])]
            reqs=[Requirement.model_validate(r) for r in state['requirements']]
            observations=list(state.get('observations',[]))
            if model:
                context={'query':request.search.text,'requirements':state['requirements'],'candidates':state['candidates'],'observations':observations[-8:],'tools':{k:v.model_json_schema() for k,v in SCHEMAS.items()}}
                plan,metadata=model.plan(context,role='challenger' if request.mode=='fitwitness' else 'planner')
                jobs.event(scope,run_id,'model',metadata)
                for op in plan.operations:
                    guard();result=tools.execute(op);observations.append({'tool':op.model_dump(mode='json'),'result':result})
                    jobs.event(scope,run_id,'tool',{'name':op.name,'arguments':op.arguments,'items':len(result) if isinstance(result,list) else 1})
                    if op.name.startswith('search_'):
                        known={c.revision_id for c in candidates}
                        for item in result:
                            c=Candidate.model_validate(item)
                            if c.revision_id not in known:candidates.append(c);known.add(c.revision_id)
            else:
                # Visible deterministic baseline, not a fabricated model execution.
                for c in candidates:
                    guard()
                    if budget.usage.tool_calls>=budget.budget.max_tool_calls:break
                    op=ToolRequest(name='query_dimensions',arguments={'revision_id':c.revision_id,'fields':[r.field for r in reqs]})
                    result=tools.execute(op);observations.append({'tool':op.model_dump(mode='json'),'result':result})
                    jobs.event(scope,run_id,'tool',{'name':op.name,'revision_id':c.revision_id,'fields':[r.field for r in reqs]})
            decisions=[verify(reqs,c,snapshot.id) for c in candidates]
            decisions.sort(key=lambda d:({'match':0,'unknown':1,'mismatch':2}[d.verdict],d.revision_id))
            jobs.event(scope,run_id,'verified',{'match':sum(d.verdict=='match' for d in decisions),'mismatch':sum(d.verdict=='mismatch' for d in decisions),'unknown':sum(d.verdict=='unknown' for d in decisions),'mode':'규칙 기반 검증' if not model else request.mode})
            return {'candidates':[c.model_dump(mode='json') for c in candidates],'decisions':[d.model_dump(mode='json') for d in decisions],'iterations':state.get('iterations',0)+1,'observations':observations,'usage':budget.usage.model_dump(mode='json')}
        def route(state):
            # ReAct may search again when evidence is missing. Deterministic baseline never impersonates model reasoning.
            unknown=any(d['verdict']=='unknown' for d in state.get('decisions',[]))
            return 'inspect' if model and request.mode!='fixed' and unknown and state['iterations']<2 and budget.usage.tool_calls<budget.budget.max_tool_calls else END
        builder=StateGraph(State);builder.add_node('intent',intent);builder.add_node('retrieve',retrieve);builder.add_node('inspect',inspect)
        builder.add_edge(START,'intent');builder.add_edge('intent','retrieve');builder.add_edge('retrieve','inspect');builder.add_conditional_edges('inspect',route)
        with PostgresSaver.from_conn_string(repo.dsn) as cp:
            cp.setup();graph=builder.compile(checkpointer=cp);config={'configurable':{'thread_id':scope.tenant_id+':'+run_id}}
            existing=graph.get_state(config)
            if existing.values:
                jobs.event(scope,run_id,'resumed',{'checkpoint_id':existing.config.get('configurable',{}).get('checkpoint_id')})
                previous=existing.values.get('usage',{})
                budget.usage=Usage.model_validate(previous)
                out=graph.invoke(None,config,durability="sync") if existing.next else existing.values
            else:out=graph.invoke({},config,durability="sync")
            jobs.finalize(scope,run_id,snapshot.id,[Decision.model_validate(d) for d in out.get('decisions',[])],token,Usage.model_validate(out.get('usage',{})))
    except Exception as exc:
        jobs.fail(scope,run_id,token,str(exc));raise
    finally:stop.set();thread.join(timeout=1)
