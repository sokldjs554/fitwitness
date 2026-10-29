"""Which code a run was started and resumed by.

A claim can wait days for a person, and the service can be deployed in between: the checkpoint was written by one
version of the code and is read by another. The runner records the version at each step (the queued event and the
``code_version`` column at the start, every answer and every resume after) so the record says which code made which
decision. The value is the deployed commit (``RENDER_GIT_COMMIT`` on Render, ``FITWITNESS_GIT_SHA`` elsewhere), the
checkout's HEAD when running from one, else ``local``.
"""

from __future__ import annotations

import os


def current() -> str:
    for name in ("RENDER_GIT_COMMIT", "FITWITNESS_GIT_SHA"):
        value = os.getenv(name, "").strip()
        if value:
            return value[:64]
    from fitwitness.evaluation.provenance import git_sha

    sha = git_sha()
    return sha if sha != "unknown" else "local"
