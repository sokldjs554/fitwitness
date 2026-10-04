import os
from pathlib import Path
from fitwitness.storage.repository import Repository
from fitwitness.runtime.jobs import Jobs
from langgraph.checkpoint.postgres import PostgresSaver

repo = Repository(os.environ["FITWITNESS_DATABASE_URL"])
repo.migrate()
Jobs(repo).migrate()
with PostgresSaver.from_conn_string(repo.dsn) as cp:
    cp.setup()
if not Path("var/corpus/manifest.json").exists():
    from fitwitness.data.generate import generate_dataset

    generate_dataset(Path("var/corpus"))
print("Database, checkpoints and source CAD ready.")
