"""Warm worker processes.

A run executes in its own process so a crash or a kill cannot take the server down and
so the demo can terminate one mid-run. Starting that process cold costs the interpreter
plus the LangGraph/LangChain/psycopg imports: about a second on a laptop, ten or more on
a shared free-tier CPU, paid on every run before any work began.

The pool keeps one or more workers already imported and idle, blocked on stdin. A job is
handed over as one JSON line; the worker runs exactly that job and exits, so the exit
code semantics of a cold worker (0, a crash, the demo's 86) are unchanged. As soon as a
worker is taken, a replacement starts warming in the background.
"""

from __future__ import annotations

import json
import subprocess
import threading


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
            while len(self.idle) < self.size:
                self.idle.append(self._spawn())

    def close(self) -> None:
        with self.lock:
            self.started = False
            idle, self.idle = self.idle, []
        for proc in idle:
            try:
                proc.stdin.close()  # an idle worker exits when its stdin closes
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    def _spawn(self) -> subprocess.Popen:
        return subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=None,  # worker tracebacks go to the server log instead of being dropped
            env=self.env,
            cwd=self.cwd,
        )

    def _take(self) -> subprocess.Popen:
        with self.lock:
            while self.idle:
                proc = self.idle.pop(0)  # the oldest is the most likely to be warm
                if proc.poll() is None:
                    break
            else:
                proc = self._spawn()
            if self.started:
                while len(self.idle) < self.size:
                    self.idle.append(self._spawn())
        return proc

    @property
    def warm(self) -> int:
        with self.lock:
            return sum(1 for p in self.idle if p.poll() is None)

    # -- jobs -------------------------------------------------------------------------
    def run(self, job: dict, *, timeout: float) -> int:
        """Run one job in a (preferably warm) worker and return its exit code.

        Raises ``subprocess.TimeoutExpired`` after killing the worker, like subprocess.run."""
        proc = self._take()
        try:
            proc.stdin.write((json.dumps(job) + "\n").encode())
            proc.stdin.flush()
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass  # the worker died before reading; its exit code tells the caller
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise
