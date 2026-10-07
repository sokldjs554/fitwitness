"""The real worker in --pool mode serves jobs handed over stdin and keeps the exit codes."""
import os, sys, time
from pathlib import Path
from uuid import uuid4
from test_workflow import env, request
from fitwitness.runtime.pool import WarmWorkers

ROOT = Path(__file__).resolve().parents[2]


def warm_pool(r):
    return WarmWorkers(
        [sys.executable, "-m", "fitwitness.runtime.worker", "--pool"],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "FITWITNESS_DATABASE_URL": r.dsn},
        cwd=ROOT,
        size=1,
    )


def test_warm_worker_completes_a_run_and_a_replacement_is_ready(env):
    r, j, s = env
    w = warm_pool(r)
    w.start()
    run = j.enqueue(s, request(), "pool-1")
    token = str(uuid4())
    started = time.monotonic()
    assert w.run({"tenant": s.tenant_id, "run": run.id, "token": token}, timeout=120) == 0
    elapsed = time.monotonic() - started
    assert j.get(s, run.id).state == "completed"
    assert w.warm == 1
    w.close()
    print(f"warm worker run: {elapsed:.2f}s")


def test_one_worker_serves_runs_back_to_back_without_a_new_process(env):
    r, j, s = env
    w = warm_pool(r)
    w.start()
    worker = w.idle[0]
    durations = []
    for i in range(3):
        run = j.enqueue(s, request(), f"pool-reuse-{i}")
        started = time.monotonic()
        assert w.run({"tenant": s.tenant_id, "run": run.id, "token": str(uuid4())}, timeout=120) == 0
        durations.append(time.monotonic() - started)
        assert j.get(s, run.id).state == "completed"
        assert w.idle and w.idle[0] is worker  # the same process answered every run
    w.close()
    print("back-to-back runs on one worker:", [f"{d:.2f}s" for d in durations])


def test_pool_worker_keeps_the_demo_crash_exit_code(env):
    r, j, s = env
    w = warm_pool(r)
    run = j.enqueue(s, request().model_copy(update={"demo_fault": True}), "pool-2")
    token = str(uuid4())
    assert w.run({"tenant": s.tenant_id, "run": run.id, "token": token, "fault": True}, timeout=120) == 86
    j.release_crashed(s, run.id, token)
    assert w.run({"tenant": s.tenant_id, "run": run.id, "token": token}, timeout=120) == 0
    kinds = [e["kind"] for e in j.events(s, run.id)]
    assert "interrupted" in kinds and "resumed" in kinds and j.get(s, run.id).state == "completed"
