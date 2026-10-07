"""Run one leased job in a separately terminable process.

Two entry modes. With explicit CLI arguments the process is cold: it starts, imports,
runs the job and exits. With ``--pool`` it imports everything first, pre-warms the
checkpointer schema check, then blocks until the server hands it one JSON job line on
stdin (see ``fitwitness.runtime.pool``). Either way the process runs exactly one job and
its exit code means the same thing (0 ok, 86 the demo's deliberate crash, else failure).
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
    p.add_argument("--pool", action="store_true", help="wait for one JSON job on stdin instead of CLI arguments")
    args = p.parse_args()
    dsn = os.environ["FITWITNESS_DATABASE_URL"]
    repo = Repository(dsn)
    if args.pool:
        try:
            repo.warm()  # the pool's first connection and the checkpointer schema check
            ensure_checkpointer(dsn)  # happen while idle, not on the run's clock
        except Exception:
            pass  # the run repeats them and reports a real failure
        line = sys.stdin.readline()
        if not line.strip():
            return  # the pool is closing
        job = json.loads(line)
        tenant, run_id, token, fault = job["tenant"], job["run"], job.get("token"), bool(job.get("fault"))
    else:
        if not (args.tenant and args.run):
            p.error("--tenant and --run are required without --pool")
        tenant, run_id, token, fault = args.tenant, args.run, args.token, args.fault
    execute_run(
        repo,
        TenantScope(tenant_id=tenant, user_id="worker", role="operator"),
        run_id,
        fault_after_retrieval=fault,
        lease_token=token,
    )


if __name__ == "__main__":
    main()
