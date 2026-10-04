import os
import pytest


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
