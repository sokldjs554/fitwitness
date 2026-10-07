"""Warm workers hand jobs to already-started processes and keep a replacement ready."""
import subprocess, sys, threading, time
import pytest
from fitwitness.runtime.pool import WarmWorkers

# One-shot worker (the original protocol): reads one job, exits with its code.
FAKE = [sys.executable, "-c", "import sys,json; j=json.loads(sys.stdin.readline() or '{}'); time=__import__('time'); time.sleep(j.get('sleep',0)); sys.exit(int(j.get('code',0)))"]

# Serving worker: answers {"done": true} after a normal job and waits for the next; a job with
# a code exits with it, like the real worker's crash.
LOOP = [sys.executable, "-c", """
import sys, json, time
while True:
    line = sys.stdin.readline()
    if not line.strip():
        break
    j = json.loads(line)
    time.sleep(j.get('sleep', 0))
    if j.get('code'):
        sys.exit(int(j['code']))
    print(json.dumps({'done': True}), flush=True)
"""]


def pool(size=1, cmd=FAKE):
    return WarmWorkers(cmd, env={}, size=size)


def test_exit_codes_pass_through_and_replacements_are_kept_warm():
    w = pool(size=2)
    w.start()
    time.sleep(0.3)
    assert w.warm == 2
    assert w.run({"code": 0}, timeout=10) == 0
    assert w.run({"code": 86}, timeout=10) == 86
    assert w.warm == 2  # every worker that ended was replaced
    w.close()
    assert w.warm == 0


def test_a_worker_that_reports_done_serves_the_next_job_without_a_new_process():
    w = pool(size=1, cmd=LOOP)
    w.start()
    first = w.idle[0]
    for _ in range(3):
        assert w.run({}, timeout=10) == 0
        assert w.warm == 1
        assert w.idle[0] is first  # the same process, not a replacement
    w.close()
    assert first.poll() is not None  # closing the pool ends it


def test_a_crash_ends_the_worker_and_warms_a_replacement():
    w = pool(size=1, cmd=LOOP)
    w.start()
    first = w.idle[0]
    assert w.run({"code": 86}, timeout=10) == 86
    assert w.warm == 1 and w.idle[0] is not first
    assert w.run({}, timeout=10) == 0  # the replacement serves the recovery
    w.close()


def test_a_job_expected_to_crash_gets_its_replacement_while_it_runs():
    w = pool(size=1, cmd=LOOP)
    w.start()
    seen = []
    threading.Timer(0.3, lambda: seen.append(w.warm)).start()
    assert w.run({"fault": True, "sleep": 0.8, "code": 86}, timeout=10) == 86
    assert seen == [1]  # a spare was warming mid-run, not only afterwards
    w.close()


def test_overflow_worker_is_released_when_the_pool_is_full():
    w = pool(size=1, cmd=LOOP)
    w.start()
    held = w.idle[0]
    results = []
    thread = threading.Thread(target=lambda: results.append(w.run({"sleep": 0.6}, timeout=10)))
    thread.start()
    time.sleep(0.2)  # the warm worker is busy; this job gets a fresh process
    assert w.run({}, timeout=10) == 0
    thread.join()
    assert results == [0]
    # The fresh process finished first and took the idle place; the original one finished
    # into a full pool and was let go. Never more than `size` idle workers.
    assert w.warm == 1
    assert w.idle[0] is not held and held.poll() is not None
    w.close()


def test_without_start_it_spawns_cold_and_leaves_nothing_idle():
    w = pool(size=1)
    assert w.run({"code": 3}, timeout=10) == 3
    assert w.warm == 0
    w = pool(size=1, cmd=LOOP)
    assert w.run({}, timeout=10) == 0
    assert w.warm == 0  # not started: the process is let go after its job


def test_dead_idle_worker_is_skipped():
    w = pool(size=1, cmd=[sys.executable, "-c", "import sys; sys.exit(7)"])
    w.start()
    time.sleep(0.5)  # the idle worker has died by now
    assert w.run({"code": 0}, timeout=10) == 7  # a fresh process was spawned instead of using a corpse
    w.close()


def test_timeout_kills_the_worker_and_the_next_job_still_runs():
    w = pool(size=0)
    with pytest.raises(subprocess.TimeoutExpired):
        w.run({"sleep": 5}, timeout=0.5)
    w = pool(size=1, cmd=LOOP)
    w.start()
    with pytest.raises(subprocess.TimeoutExpired):
        w.run({"sleep": 5}, timeout=0.5)
    assert w.warm == 1  # the killed worker was replaced
    assert w.run({}, timeout=10) == 0
    w.close()
