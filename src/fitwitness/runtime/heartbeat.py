"""The lease heartbeat shared by the drawing and claim runs.

A run holds a 30 s lease and renews it every 10 s. Losing the lease (another worker took over, or the run was cancelled)
must stop the run at its next guard; a single failed renewal (a dropped database connection, a restart of the pooler) must
not: the lease still has most of its time, the next renewal usually works, and every write the run makes is fenced by the
lease token in the database anyway, so a run that really lost it cannot do harm while it finds out."""

from __future__ import annotations

import threading


def start_heartbeat(
    jobs, scope, run_id: str, token: str, stop: threading.Event, lost: threading.Event, *, interval: float = 10, tolerated_errors: int = 2
) -> threading.Thread:
    def beat():
        errors = 0
        while not stop.wait(interval):
            try:
                if not jobs.heartbeat(scope, run_id, token):
                    lost.set()  # the database says another owner holds it, or the run is no longer running
                    return
                errors = 0
            except Exception:  # noqa: BLE001 - a failed renewal is not a lost lease until it keeps failing
                errors += 1
                if errors > tolerated_errors:
                    lost.set()
                    return

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    return thread
