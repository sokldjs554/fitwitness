"""The lease heartbeat: one failed renewal is survived, a lost lease or a run of failures is reported."""

import threading

from fitwitness.runtime.heartbeat import start_heartbeat


class Jobs:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    def heartbeat(self, scope, run_id, token):
        self.calls += 1
        answer = self.answers.pop(0) if self.answers else True
        if isinstance(answer, Exception):
            raise answer
        return answer


def _run(answers, wait_calls):
    jobs, stop, lost = Jobs(answers), threading.Event(), threading.Event()
    thread = start_heartbeat(jobs, None, "r", "t", stop, lost, interval=0.005, tolerated_errors=2)
    for _ in range(400):  # up to 2 s for the calls to happen
        if jobs.calls >= wait_calls or lost.is_set():
            break
        stop.wait(0.005)
    stop.set()
    thread.join(timeout=1)
    return lost.is_set()


def test_a_single_failed_renewal_does_not_stop_the_run():
    assert _run([RuntimeError("connection reset"), True, True], 4) is False


def test_two_failures_in_a_row_are_survived_and_a_success_resets_the_count():
    assert _run([RuntimeError("x"), RuntimeError("y"), True, RuntimeError("z"), RuntimeError("w"), True], 7) is False


def test_a_lease_the_database_says_is_gone_stops_the_run_at_once():
    assert _run([True, False], 5) is True


def test_a_database_that_keeps_failing_is_reported_as_a_lost_lease():
    assert _run([RuntimeError("down")] * 10, 10) is True
