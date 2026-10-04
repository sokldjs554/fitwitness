from test_workflow import env, request
from fitwitness.contracts import Usage, TenantScope
from uuid import uuid4
import pytest


def test_expired_worker_cannot_commit_or_renew(env):
    r, j, s = env
    run = j.enqueue(s, request(), "expired")
    token = j.claim(s, run.id, lease_seconds=-1)
    assert not j.finalize(s, run.id, run.snapshot_id, [], token)
    assert not j.heartbeat(s, run.id, token)


def test_lease_guards_usage_and_events(env):
    r, j, s = env
    run = j.enqueue(s, request(), "fence")
    old = j.claim(s, run.id, lease_seconds=-1)
    new = j.claim(s, run.id)
    with pytest.raises(RuntimeError):
        j.save_usage(s, run.id, old, Usage(model_calls=1))
    with pytest.raises(RuntimeError):
        j.event(s, run.id, "tool", {}, token=old)
    j.save_usage(s, run.id, new, Usage(model_calls=1))
    j.fail(s, run.id, new, "network failure")
    assert j.get(s, run.id).usage.model_calls == 1


def test_completed_read_detects_missing_invalidation(env):
    from fitwitness.agents.graph import execute_run

    r, j, s = env
    run = j.enqueue(s, request(), "stale")
    execute_run(r, s, run.id)
    rid = r.snapshot(s).revision_ids[0]
    r.save_facts(s, rid, [])
    assert j.get(s, run.id).state == "stale"


def test_queued_job_is_discoverable_after_restart(env):
    from fitwitness.agents.graph import execute_run

    r, j, s = env
    run = j.enqueue(s, request(), "pending")
    assert (s.tenant_id, run.id) in j.pending()
    execute_run(r, s, run.id)
    assert (s.tenant_id, run.id) not in j.pending()


def test_shared_admission_limit_is_enforced(env):
    r, j, s = env
    key = str(uuid4())
    assert j.admit(key, 2)
    assert j.admit(key, 2)
    assert not j.admit(key, 2)

def test_dispatcher_recovers_abnormal_process_exit(env):
    import os,subprocess,sys,time
    from fitwitness.runtime.dispatcher import Dispatcher
    r,j,s=env;run=j.enqueue(s,request(),'abnormal-exit')
    child="from fitwitness.storage.repository import Repository;from fitwitness.runtime.jobs import Jobs;from fitwitness.contracts import TenantScope;import os;Jobs(Repository(os.environ['FITWITNESS_DATABASE_URL'])).claim(TenantScope(tenant_id=os.environ['TEST_TENANT'],user_id='dead'),os.environ['TEST_RUN'],lease_seconds=-1);os._exit(7)"
    child_env={**os.environ,'PYTHONPATH':'src','FITWITNESS_DATABASE_URL':r.dsn,'TEST_TENANT':s.tenant_id,'TEST_RUN':run.id}
    assert subprocess.run([sys.executable,'-c',child],env=child_env).returncode==7
    def supervise(scope,rid,fault=False):
        subprocess.run([sys.executable,'-m','fitwitness.runtime.worker','--tenant',scope.tenant_id,'--run',rid],env=child_env,check=True)
    dispatcher=Dispatcher(j,supervise);dispatcher.start()
    try:
        deadline=time.monotonic()+20
        while time.monotonic()<deadline and j.get(s,run.id).state!='completed':time.sleep(.1)
        assert j.get(s,run.id).state=='completed'
        assert sum(e['kind']=='completed' for e in j.events(s,run.id))==1
    finally:dispatcher.close()
