"""Which code produced a record: the commit, and whether the tree had changes the commit does not contain."""

from __future__ import annotations

import os
import subprocess


def git_sha(*, dirty_suffix: bool = False) -> str:
    """HEAD of the checkout; ``$GITHUB_SHA`` or ``"unknown"`` outside one.

    With ``dirty_suffix`` a tree whose tracked files differ from HEAD reads ``<sha>-dirty``: a record written from it names the
    commit it was built on, not code that any commit contains."""
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001 - not inside a checkout (a source archive, an installed package)
        return os.getenv("GITHUB_SHA", "unknown")
    if dirty_suffix:
        try:
            if subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=no"], text=True, stderr=subprocess.DEVNULL
            ).strip():
                return sha + "-dirty"
        except Exception:  # noqa: BLE001
            pass
    return sha
