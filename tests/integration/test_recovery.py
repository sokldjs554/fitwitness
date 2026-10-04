import os, subprocess, sys
from test_workflow import env, request


def test_real_worker_crash_resumes_without_repeating_retrieval(env):
    r, j, s = env
    run = j.enqueue(s, request(), "crash")
    command = [
        sys.executable,
        "-m",
        "fitwitness.runtime.worker",
        "--tenant",
        s.tenant_id,
        "--run",
        run.id,
        "--fault",
    ]
    proc = subprocess.run(
        command,
        env={**os.environ, "PYTHONPATH": "src", "FITWITNESS_DATABASE_URL": r.dsn},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 86, proc.stderr
    assert j.get(s, run.id).state == "running"
    j.release_crashed(s, run.id)
    resumed = subprocess.run(
        command[:-1],
        env={**os.environ, "PYTHONPATH": "src", "FITWITNESS_DATABASE_URL": r.dsn},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert resumed.returncode == 0, resumed.stderr
    assert j.get(s, run.id).state == "completed"
    kinds = [e["kind"] for e in j.events(s, run.id)]
    assert kinds.count("retrieved") == 1
    assert kinds.count("completed") == 1
    assert "interrupted" in kinds and "resumed" in kinds


def test_old_worker_fencing_token_cannot_complete(env):
    r, j, s = env
    run = j.enqueue(s, request(), "fence")
    old = j.claim(s, run.id)
    j.release_crashed(s, run.id)
    new = j.claim(s, run.id)
    assert new and new != old
    assert not j.finalize(s, run.id, run.snapshot_id, [], old)
    assert j.get(s, run.id).state == "running"
