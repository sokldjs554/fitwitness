from test_workflow import env,request
from fitwitness.contracts import Usage,TenantScope
from uuid import uuid4
import pytest

def test_expired_worker_cannot_commit_or_renew(env):
    r,j,s=env;run=j.enqueue(s,request(),'expired');token=j.claim(s,run.id,lease_seconds=-1)
    assert not j.finalize(s,run.id,run.snapshot_id,[],token)
    assert not j.heartbeat(s,run.id,token)

def test_lease_guards_usage_and_events(env):
    r,j,s=env;run=j.enqueue(s,request(),'fence');old=j.claim(s,run.id,lease_seconds=-1);new=j.claim(s,run.id)
    with pytest.raises(RuntimeError):j.save_usage(s,run.id,old,Usage(model_calls=1))
    with pytest.raises(RuntimeError):j.event(s,run.id,'tool',{},token=old)
    j.save_usage(s,run.id,new,Usage(model_calls=1));j.fail(s,run.id,new,'network failure')
    assert j.get(s,run.id).usage.model_calls==1

def test_completed_read_detects_missing_invalidation(env):
    from fitwitness.agents.graph import execute_run
    r,j,s=env;run=j.enqueue(s,request(),'stale');execute_run(r,s,run.id)
    rid=r.snapshot(s).revision_ids[0];r.save_facts(s,rid,[])
    assert j.get(s,run.id).state=='stale'

def test_queued_job_is_discoverable_after_restart(env):
    from fitwitness.agents.graph import execute_run
    r,j,s=env;run=j.enqueue(s,request(),'pending')
    assert (s.tenant_id,run.id) in j.pending()
    execute_run(r,s,run.id)
    assert (s.tenant_id,run.id) not in j.pending()

def test_shared_admission_limit_is_enforced(env):
    r,j,s=env;key=str(uuid4());assert j.admit(key,2);assert j.admit(key,2);assert not j.admit(key,2)
