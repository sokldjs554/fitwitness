import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)  # the tests read var/ and docs/ by relative path: they run from the repository root wherever pytest was started

# Tests that read the generated drawing corpus (python scripts/setup.py). Without it they are skipped, not failed.
NEEDS_DRAWING_CORPUS = {"test_ingest.py", "test_retrieval_metrics.py", "test_retrieval_ablation.py"}


def pytest_collection_modifyitems(config, items):
    if (ROOT / "var/corpus/manifest.json").exists():
        return
    skip = pytest.mark.skip(reason="drawing corpus not generated (run: python scripts/setup.py)")
    for item in items:
        if Path(str(item.fspath)).name in NEEDS_DRAWING_CORPUS:
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def initialize_schema(request):
    if not any("/integration/" in str(item.fspath) for item in request.session.items):
        return
    from fitwitness.storage.repository import Repository
    from fitwitness.runtime.jobs import Jobs

    repo = Repository(
        os.getenv(
            "FITWITNESS_DATABASE_URL",
            "postgresql://fwadmin@/postgres?host=/tmp&port=55439",
        )
    )
    repo.migrate()
    Jobs(repo).migrate()
