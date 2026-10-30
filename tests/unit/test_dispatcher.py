"""The dispatcher's two slots, without a database: a finished run must hand its slot to the next queued run."""

import threading
import time

from fitwitness.runtime.dispatcher import Dispatcher


class Queue:
    """The part of Jobs the dispatcher reads: what is pending and in which state."""

    def __init__(self, n):
        self.runs = [("tenant", f"run-{i}") for i in range(n)]
        self.done = set()
        self.lock = threading.Lock()

    def escalate_overdue(self):
        pass

    def pending(self):
        with self.lock:
            return [r for r in self.runs if r[1] not in self.done]

    def raw(self, scope, run_id):
        with self.lock:
            return {"state": "completed" if run_id in self.done else "queued", "lease_until": None, "request": {}}

    def drop_dispatch(self, scope, run_id):
        pass


def test_a_finished_run_starts_the_next_queued_one_without_waiting_for_the_poll():
    queue = Queue(6)
    overlap = {"now": 0, "max": 0}

    def supervise(scope, run_id, fault=False):
        with queue.lock:
            overlap["now"] += 1
            overlap["max"] = max(overlap["max"], overlap["now"])
        time.sleep(0.05)
        with queue.lock:
            overlap["now"] -= 1
            queue.done.add(run_id)

    dispatcher = Dispatcher(queue, supervise)
    dispatcher.start()
    started = time.monotonic()
    dispatcher.nudge()  # one enqueue; nothing else wakes the loop
    try:
        while len(queue.done) < 6 and time.monotonic() - started < 5:
            time.sleep(0.01)
        elapsed = time.monotonic() - started
    finally:
        dispatcher.close()
    assert len(queue.done) == 6
    # Three rounds of two 0.05 s runs. Waiting for the one-second poll between rounds would take two seconds.
    assert elapsed < 1.0, elapsed
    assert overlap["max"] <= 2
