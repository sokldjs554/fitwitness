"""Persistent queue recovery, bounded to two subprocesses per server."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Thread, Event
import logging
from fitwitness.contracts import TenantScope


class Dispatcher:
    def __init__(self, jobs, supervise):
        self.jobs = jobs
        self.supervise = supervise
        self.stop = Event()
        self.thread = None

    def start(self):
        self.thread = Thread(target=self.loop, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)

    def loop(self):
        active = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            while not self.stop.wait(1):
                try:
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
                        active[(tenant, run_id)] = pool.submit(
                            self.supervise,
                            scope,
                            run_id,
                            raw["request"].get("demo_fault", False),
                        )
                except Exception:
                    logging.getLogger(__name__).exception("dispatcher cycle failed")
