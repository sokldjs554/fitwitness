"""Run leased jobs in a separately terminable process.

Two entry modes. With explicit CLI arguments the process is cold: it starts, imports,
runs the job and exits. With ``--pool`` it imports everything first, pre-warms the
connection pools and the checkpointer schema check, then blocks until the server hands it
a JSON job line on stdin (see ``fitwitness.runtime.pool``). After a job that ends normally
it answers ``{"done": true}`` on a private pipe and waits for the next one; a job that
raises, or the demo's deliberate crash, ends the process. Either way the exit code means
the same thing (0 ok, 86 the demo's deliberate crash, else failure).
"""

import argparse, json, os, sys
from fitwitness.contracts import TenantScope
from fitwitness.storage.repository import Repository
from fitwitness.agents.graph import execute_run, ensure_checkpointer


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tenant")
    p.add_argument("--run")
    p.add_argument("--fault", action="store_true")
    p.add_argument("--token")
    p.add_argument("--pool", action="store_true", help="serve JSON jobs from stdin instead of CLI arguments")
    args = p.parse_args()
    dsn = os.environ["FITWITNESS_DATABASE_URL"]
    repo = Repository(dsn)
    if args.pool:
        # Protocol lines go to the original stdout; anything a library prints must not
        # reach it, so fd 1 is pointed at stderr for the rest of the process.
        proto = os.fdopen(os.dup(1), "w", buffering=1)
        os.dup2(2, 1)
        try:
            repo.warm()  # the pools' first connections and the checkpointer schema check
            ensure_checkpointer(dsn)  # happen while idle, not on the run's clock
            import fitwitness.retrieval.embeddings  # noqa: F401  (imported lazily by a run otherwise)
            import fitwitness.claims.graph  # noqa: F401
        except Exception:
            pass  # the run repeats them and reports a real failure
        while True:
            line = sys.stdin.readline()
            if not line.strip():
                return  # the pool is closing
            job = json.loads(line)
            run_job(repo, job["tenant"], job["run"], job.get("token"), bool(job.get("fault")))
            proto.write(json.dumps({"done": True}) + "\n")
    else:
        if not (args.tenant and args.run):
            p.error("--tenant and --run are required without --pool")
        run_job(repo, args.tenant, args.run, args.token, args.fault)


def run_job(repo, tenant, run_id, token, fault):
    scope = TenantScope(tenant_id=tenant, user_id="worker", role="operator")
    try:
        execute_run(repo, scope, run_id, fault_after_retrieval=fault, lease_token=token)
    finally:
        # A run that raised is exactly the one worth seeing in a trace viewer.
        if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
            from fitwitness.runtime.jobs import Jobs
            from fitwitness.runtime.otlp import export_run

            export_run(Jobs(repo), scope, run_id)


if __name__ == "__main__":
    main()
