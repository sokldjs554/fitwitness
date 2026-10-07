"""Warm worker processes.

A run executes in its own process so a crash or a kill cannot take the server down and
so the demo can terminate one mid-run. Starting that process cold costs the interpreter
plus the LangGraph/LangChain/psycopg imports: about a second on a laptop, ten to twenty on
a shared free-tier CPU, paid before any work began.

The pool keeps workers already imported and idle, blocked on stdin. A job is handed over
as one JSON line. When the run ends normally the worker answers with one ``{"done": true}``
line and waits for the next job, so the cost of starting a process is paid once per worker
rather than once per run (a replacement started after every run competed with the next run
for the same CPU). Any other end keeps the old meaning: the process exits and its exit code
is the result (0, a crash, the demo's deliberate 86), and a replacement is warmed.
"""

from __future__ import annotations

import json
import subprocess
import threading


def _is_done(line: bytes) -> bool:
    try:
        return json.loads(line).get("done") is True
    except (ValueError, AttributeError):
        return False


class WarmWorkers:
    def __init__(self, command: list[str], *, env: dict, cwd=None, size: int = 1):
        self.command = command
        self.env = env
        self.cwd = cwd
        self.size = max(0, size)
        self.idle: list[subprocess.Popen] = []
        self.lock = threading.Lock()
        self.started = False

    # -- lifecycle -------------------------------------------------------------------
    def start(self) -> None:
        """Pre-warm ``size`` workers. Without start(), run() behaves like a cold spawn."""
        with self.lock:
            self.started = True
            self._top_up()

    def close(self) -> None:
        with self.lock:
            self.started = False
            idle, self.idle = self.idle, []
        for proc in idle:
            self._retire(proc)

    def _spawn(self) -> subprocess.Popen:
        return subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,  # the worker's protocol lines; its own output goes to stderr
            stderr=None,  # worker tracebacks go to the server log instead of being dropped
            env=self.env,
            cwd=self.cwd,
        )

    def _top_up(self) -> None:
        """Keep ``size`` live idle workers. Caller holds the lock."""
        self.idle = [p for p in self.idle if p.poll() is None]
        while len(self.idle) < self.size:
            self.idle.append(self._spawn())

    @staticmethod
    def _retire(proc: subprocess.Popen) -> None:
        """Let a worker go: closing its stdin ends its loop; kill it if it does not."""
        try:
            proc.stdin.close()
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
            proc.wait()
        finally:
            if proc.stdout:
                proc.stdout.close()

    def _take(self, spare: bool) -> subprocess.Popen:
        with self.lock:
            while self.idle:
                proc = self.idle.pop(0)  # the oldest is the most likely to be warm
                if proc.poll() is None:
                    break
            else:
                proc = self._spawn()  # nothing warm: this job pays for the start
            if spare and self.started:
                # A job that is expected to die (the demo's crash) gets its replacement
                # warming while it runs, so the recovery does not wait for a cold start.
                self._top_up()
        return proc

    def _release(self, proc: subprocess.Popen) -> None:
        with self.lock:
            if self.started and proc.poll() is None:
                live = sum(1 for p in self.idle if p.poll() is None)
                if live < self.size:
                    self.idle.append(proc)
                    return
            if self.started:
                self._top_up()
        self._retire(proc)

    def _replace(self) -> None:
        with self.lock:
            if self.started:
                self._top_up()

    @property
    def warm(self) -> int:
        with self.lock:
            return sum(1 for p in self.idle if p.poll() is None)

    # -- jobs -------------------------------------------------------------------------
    def run(self, job: dict, *, timeout: float) -> int:
        """Run one job in a (preferably warm) worker and return its exit code.

        A worker that reports ``done`` stays alive for the next job and the result is 0.
        Raises ``subprocess.TimeoutExpired`` after killing the worker, like subprocess.run."""
        proc = self._take(spare=bool(job.get("fault")))
        timed_out = threading.Event()

        def expire():
            timed_out.set()
            proc.kill()

        timer = threading.Timer(timeout, expire)
        timer.daemon = True
        timer.start()
        finished = False
        try:
            try:
                proc.stdin.write((json.dumps(job) + "\n").encode())
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass  # the worker died before reading; its exit code tells the caller
            for line in iter(proc.stdout.readline, b""):
                if _is_done(line):
                    finished = True
                    break
        finally:
            timer.cancel()
        if finished:
            self._release(proc)
            return 0
        if timed_out.is_set():
            proc.kill()
            proc.wait()
            self._replace()
            raise subprocess.TimeoutExpired(self.command, timeout)
        code = proc.wait()  # the output closed: the process ended and its exit code is the result
        self._replace()
        return code
