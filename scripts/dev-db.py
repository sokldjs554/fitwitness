"""Start a throwaway PostgreSQL 16 with pgvector for local runs and for the tests.

    uv sync --extra dev
    uv run python scripts/dev-db.py            # prints the export line and keeps the database up until Ctrl-C
    export FITWITNESS_DATABASE_URL=...         # in another terminal, then: uv run pytest -q

It uses the `pgserver` dev dependency (Python 3.12 only; on 3.13 use `docker compose up -d postgres` instead, which serves the
URL in .env.example). The data directory defaults to var/dev-db and survives restarts."""
import sys
import time
from pathlib import Path

import pgserver


def main() -> None:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "var/dev-db").resolve()
    server = pgserver.get_server(target, cleanup_mode=None)
    print(f"export FITWITNESS_DATABASE_URL='{server.get_uri()}'", flush=True)
    print("database is up; Ctrl-C stops this script (the data stays in " + str(target) + ")", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
