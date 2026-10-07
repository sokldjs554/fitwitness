"""Persistent queue recovery, bounded to two subprocesses per server."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Thread, Event
import logging
import time
from fitwitness.contracts import TenantScope


class Dispatcher:
    SWEEP_SECONDS = 30.0

    def __init__(self, jobs, supervise):
        self.jobs = jobs
        self.supervise = supervise
        self.stop = Event()
        self.wake = Event()  # set by the API after an enqueue so a run does not wait for the next poll
        self.thread = None

    def start(self):
        self.thread = Thread(target=self.loop, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        self.thread.join(timeout=3)

    def nudge(self):
        self.wake.set()

    def loop(self):
        active = {}
        last_sweep = 0.0
        with ThreadPoolExecutor(max_workers=2) as pool:
            while not self.stop.is_set():
                self.wake.wait(1)
                self.wake.clear()
                if self.stop.is_set():
                    break
                try:
                    if time.monotonic() - last_sweep >= self.SWEEP_SECONDS:
                        last_sweep = time.monotonic()
                        self.jobs.escalate_overdue()  # reviews nobody answered in time move to senior handling
                    active = {k: f for k, f in active.items() if not f.done()}
                    for tenant, run_id in self.jobs.pending():
                        scope = TenantScope(tenant_id=tenant, user_id="dispatcher")
                        raw = self.jobs.raw(scope, run_id)
                        if not raw or raw["state"] not in (
                            "queued",
                            "running",
                            "retry_wait",
                        ):
                            self.jobs.drop_dispatch(scope, run_id)
                            continue
                        if len(active) >= 2:
                            break
                        if (tenant, run_id) in active:
                            continue
                        if raw["lease_until"] and raw["lease_until"] > datetime.now(
                            timezone.utc
                        ):
                            continue
                        if raw.get("next_attempt_at") and raw["next_attempt_at"] > datetime.now(timezone.utc):
                            continue  # backing off; the retry is not due yet
                        active[(tenant, run_id)] = pool.submit(
                            self.supervise,
                            scope,
                            run_id,
                            # Once the deliberate crash happened the recovery runs normally.
                            bool(raw["request"].get("demo_fault", False)) and not raw.get("fault_consumed"),
                        )
                except Exception:
                    logging.getLogger(__name__).exception("dispatcher cycle failed")
