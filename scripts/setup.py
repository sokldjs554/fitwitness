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
if not Path("var/claims/manifest.json").exists():
    from fitwitness.claims.synth import generate_cases

    generate_cases(Path("var/claims"), n=120)
print("Database, checkpoints, source CAD and synthetic claims ready.")
