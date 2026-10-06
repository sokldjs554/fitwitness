"""Warm workers hand one job to an already-started process and keep a replacement ready."""
import subprocess, sys, time
import pytest
from fitwitness.runtime.pool import WarmWorkers

FAKE = [sys.executable, "-c", "import sys,json; j=json.loads(sys.stdin.readline() or '{}'); time=__import__('time'); time.sleep(j.get('sleep',0)); sys.exit(int(j.get('code',0)))"]


def pool(size=1, cmd=FAKE):
    return WarmWorkers(cmd, env={}, size=size)


def test_exit_codes_pass_through_and_replacements_are_kept_warm():
    w = pool(size=2)
    w.start()
    time.sleep(0.3)
    assert w.warm == 2
    assert w.run({"code": 0}, timeout=10) == 0
    assert w.run({"code": 86}, timeout=10) == 86
    assert w.warm == 2  # every taken worker was replaced
    w.close()
    assert w.warm == 0


def test_without_start_it_spawns_cold_and_leaves_nothing_idle():
    w = pool(size=1)
    assert w.run({"code": 3}, timeout=10) == 3
    assert w.warm == 0


def test_dead_idle_worker_is_skipped():
    w = pool(size=1, cmd=[sys.executable, "-c", "import sys; sys.exit(7)"])
    w.start()
    time.sleep(0.5)  # the idle worker has died by now
    assert w.run({"code": 0}, timeout=10) == 7  # a fresh process was spawned instead of using a corpse
    w.close()


def test_timeout_kills_the_worker():
    w = pool(size=0)
    with pytest.raises(subprocess.TimeoutExpired):
        w.run({"sleep": 5}, timeout=0.5)
