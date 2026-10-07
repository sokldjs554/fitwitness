"""PostgreSQL repository. All data reads use a transaction-scoped RLS role."""

from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import threading
import time
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

# One pool per DSN per process. Every request-scoped operation used to open its own TLS
# connection; against a remote database that cost more than the query itself.
_POOLS: dict[str, ConnectionPool] = {}
_CHECKPOINT_POOLS: dict[str, ConnectionPool] = {}
_POOLS_LOCK = threading.Lock()
_IDLE_PING_SECONDS = 30.0


def _check_when_idle(conn) -> None:
    """Ping a pooled connection only when it has been idle long enough to have been dropped.

    psycopg_pool's default check costs one round trip per borrow, which on a remote database
    is as expensive as the query it protects. A connection used within the last few seconds
    is alive; one idle for longer (a suspended serverless database, a closed TCP session) is
    verified before it is handed out."""
    if time.monotonic() - getattr(conn, "_fw_used", 0.0) > _IDLE_PING_SECONDS:
        ConnectionPool.check_connection(conn)


def _touch(conn) -> None:
    conn._fw_used = time.monotonic()


def pool_for(dsn: str) -> ConnectionPool:
    with _POOLS_LOCK:
        pool = _POOLS.get(dsn)
        if pool is None or pool.closed:
            pool = ConnectionPool(
                dsn,
                min_size=1,
                max_size=int(os.getenv("FITWITNESS_DB_POOL_MAX", "8")),
                max_idle=120,
                # Autocommit: ``connection()`` and ``plain()`` open and close their own
                # transaction with queued BEGIN/COMMIT statements (see there).
                kwargs={"row_factory": dict_row, "autocommit": True},
                check=_check_when_idle,
                open=True,
                name=f"fitwitness-{len(_POOLS)}",
            )
            _POOLS[dsn] = pool
        return pool


def checkpoint_pool_for(dsn: str) -> ConnectionPool:
    """Autocommit connections for the LangGraph checkpointer, kept open between runs.

    The checkpointer used to connect for every run; a worker that stays alive between jobs
    pays that handshake (TLS, authentication, a few round trips) only once this way. The
    connection settings mirror ``PostgresSaver.from_conn_string``."""
    with _POOLS_LOCK:
        pool = _CHECKPOINT_POOLS.get(dsn)
        if pool is None or pool.closed:
            pool = ConnectionPool(
                dsn,
                min_size=1,
                max_size=4,
                max_idle=120,
                kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
                check=_check_when_idle,
                open=True,
                name=f"fitwitness-checkpoint-{len(_CHECKPOINT_POOLS)}",
            )
            _CHECKPOINT_POOLS[dsn] = pool
        return pool


@contextmanager
def checkpoint_connection(dsn: str):
    with checkpoint_pool_for(dsn).connection() as c:
        try:
            yield c
        finally:
            _touch(c)


from fitwitness.contracts import DrawingRevision, Fact, SearchSnapshot, TenantScope

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='fitwitness_app') THEN CREATE ROLE fitwitness_app NOLOGIN NOSUPERUSER NOBYPASSRLS; END IF; END $$;
CREATE TABLE IF NOT EXISTS fw_revisions (
 tenant_id text NOT NULL, id text NOT NULL, document_id text NOT NULL,
 supersedes text, data jsonb NOT NULL,
 PRIMARY KEY(tenant_id,id),
 FOREIGN KEY(tenant_id,supersedes) REFERENCES fw_revisions(tenant_id,id)
);
CREATE TABLE IF NOT EXISTS fw_assets (
 tenant_id text NOT NULL, revision_id text NOT NULL, kind text NOT NULL, data bytea NOT NULL,
 PRIMARY KEY(tenant_id,revision_id,kind), FOREIGN KEY(tenant_id,revision_id) REFERENCES fw_revisions(tenant_id,id)
);
CREATE TABLE IF NOT EXISTS fw_facts (
 tenant_id text NOT NULL, revision_id text NOT NULL, id text NOT NULL, data jsonb NOT NULL,
 PRIMARY KEY(tenant_id,id), FOREIGN KEY(tenant_id,revision_id) REFERENCES fw_revisions(tenant_id,id)
);
"""


class Repository:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def migrate(self):
        with psycopg.connect(self.dsn, autocommit=True) as c:
            c.execute(SCHEMA)
            # PostgreSQL 16 role creators can have ADMIN without SET (e.g. Neon).
            # Keep request queries in the non-bypass role instead of using owner rights.
            c.execute("GRANT fitwitness_app TO CURRENT_USER WITH SET TRUE")
            c.execute(
                "CREATE TABLE IF NOT EXISTS fw_vectors (tenant_id text NOT NULL, revision_id text NOT NULL, channel text NOT NULL, embedding vector NOT NULL, PRIMARY KEY(tenant_id,revision_id,channel), FOREIGN KEY(tenant_id,revision_id) REFERENCES fw_revisions(tenant_id,id))"
            )
            c.execute("ALTER TABLE fw_vectors ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb")
            c.execute(
                "CREATE TABLE IF NOT EXISTS fw_saved_tools (tenant_id text NOT NULL, name text NOT NULL, definition jsonb NOT NULL, "
                "created_by_run text, created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,name))"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS fw_claim_cases (tenant_id text NOT NULL, case_id text NOT NULL, data jsonb NOT NULL, "
                "created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,case_id))"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS fw_claim_docs (tenant_id text NOT NULL, id text NOT NULL, case_id text NOT NULL, kind text NOT NULL, "
                "issued_at text NOT NULL, pdf bytea NOT NULL, png bytea, PRIMARY KEY(tenant_id,id))"
            )
            # The payout ledger: one row per claim, one row per dedupe key. The unique keys are what
            # makes paying idempotent: a node re-run after a crash cannot insert a second time.
            c.execute(
                "CREATE TABLE IF NOT EXISTS fw_payouts (tenant_id text NOT NULL, claim_id text NOT NULL, policy_id text NOT NULL, "
                "amount bigint NOT NULL, line_items jsonb NOT NULL, run_id text, paid_at timestamptz NOT NULL DEFAULT now(), "
                "PRIMARY KEY(tenant_id,claim_id))"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS fw_payout_keys (tenant_id text NOT NULL, dedupe_key text NOT NULL, claim_id text NOT NULL, "
                "policy_id text NOT NULL, PRIMARY KEY(tenant_id,dedupe_key))"
            )
            for name in ["fw_revisions", "fw_assets", "fw_facts", "fw_vectors", "fw_saved_tools",
                         "fw_claim_cases", "fw_claim_docs", "fw_payouts", "fw_payout_keys"]:
                c.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
                c.execute(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY")
                if not c.execute(
                    "SELECT 1 FROM pg_policies WHERE tablename=%s AND policyname=%s",
                    (name, "tenant_scope"),
                ).fetchone():
                    c.execute(
                        f"CREATE POLICY tenant_scope ON {name} USING (tenant_id=current_setting('app.tenant',true)) WITH CHECK (tenant_id=current_setting('app.tenant',true))"
                    )
                c.execute(
                    f"GRANT SELECT,INSERT,UPDATE,DELETE ON {name} TO fitwitness_app"
                )

    def warm(self) -> None:
        """Open both pools now (an idle worker does this before a job arrives)."""
        pool_for(self.dsn)
        checkpoint_pool_for(self.dsn)

    @contextmanager
    def connection(self, scope: TenantScope):
        """A pooled connection inside one transaction, as the non-bypass role, scoped to the tenant.

        Both settings are transaction-local, so they vanish when the transaction commits or
        rolls back at exit; the next borrower starts from the owner role with no tenant.

        The block is one exchange with the server. The connection runs in libpq pipeline
        mode and the transaction is opened and closed with queued BEGIN and COMMIT
        statements rather than psycopg's transaction handling, which syncs after its own
        BEGIN: this way BEGIN, the role switch, the statements and COMMIT travel together,
        and the round trip is paid when a result is read or the block ends. Errors surface
        at that point with the same exception types as before; the transaction is then
        rolled back."""
        with self._pipelined() as c:
            c.execute(
                "SELECT set_config('role','fitwitness_app',true), set_config('app.tenant',%s,true)",
                (scope.tenant_id,),
            )
            yield c

    @contextmanager
    def plain(self):
        """A pooled connection as the owner role, for tables without row-level security."""
        with self._pipelined() as c:
            yield c

    @contextmanager
    def _pipelined(self):
        pool = pool_for(self.dsn)
        c = pool.getconn()
        try:
            try:
                with c.pipeline():
                    c.execute("BEGIN")
                    yield c
                    c.execute("COMMIT")
            except BaseException:
                # A failed statement leaves the transaction aborted (its COMMIT was skipped);
                # an exception from the caller leaves it open. Either way, end it.
                if c.pgconn.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                    c.rollback()
                raise
        finally:
            _touch(c)
            pool.putconn(c)

    @staticmethod
    def lock(c, tenant_id):
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (tenant_id,))

    def add_revision(
        self, scope: TenantScope, revision: DrawingRevision, payload: bytes
    ) -> str:
        if scope.role == "viewer" or revision.tenant_id != scope.tenant_id:
            raise PermissionError("write scope mismatch")
        if sha256(payload).hexdigest() != revision.source_hash:
            raise ValueError("source hash mismatch")
        with self.connection(scope) as c:
            self.lock(c, scope.tenant_id)
            old = c.execute(
                "SELECT data FROM fw_revisions WHERE id=%s", (revision.id,)
            ).fetchone()
            if old:
                if old["data"] != revision.model_dump(mode="json"):
                    raise ValueError("revision is immutable")
                return revision.id
            if revision.supersedes:
                parent = c.execute(
                    "SELECT document_id FROM fw_revisions WHERE id=%s",
                    (revision.supersedes,),
                ).fetchone()
                if not parent or parent["document_id"] != revision.document_id:
                    raise ValueError(
                        "supersedes must refer to an existing revision of this document"
                    )
            c.execute(
                "INSERT INTO fw_revisions VALUES (%s,%s,%s,%s,%s)",
                (
                    scope.tenant_id,
                    revision.id,
                    revision.document_id,
                    revision.supersedes,
                    Jsonb(revision.model_dump(mode="json")),
                ),
            )
            c.execute(
                "INSERT INTO fw_assets VALUES (%s,%s,%s,%s)",
                (scope.tenant_id, revision.id, "pdf", payload),
            )
        return revision.id

    def seed_revision(self, scope: TenantScope, revision: DrawingRevision, payload: bytes, facts: list[Fact],
                      assets: dict[str, bytes]) -> bool:
        """add_revision + save_facts + put_asset in one transaction (demo workspace seeding).

        Returns False when the revision already exists. Same checks as the separate methods."""
        return self.seed_revisions(scope, [(revision, payload, facts, assets)]) == 1

    def seed_revisions(self, scope: TenantScope,
                       items: list[tuple[DrawingRevision, bytes, list[Fact], dict[str, bytes]]]) -> int:
        """Several drawings (revision, PDF, facts, other assets) in one transaction.

        A demo workspace is twenty drawings; one transaction and one existence query for all
        of them costs a couple of round trips instead of a couple per drawing. Revisions that
        already exist are skipped; a revision may supersede one seeded earlier in the same
        batch. Returns how many were new."""
        for revision, payload, facts, _ in items:
            if scope.role == "viewer" or revision.tenant_id != scope.tenant_id:
                raise PermissionError("write scope mismatch")
            if sha256(payload).hexdigest() != revision.source_hash:
                raise ValueError("source hash mismatch")
            for f in facts:
                if f.source.revision_id != revision.id or f.source.source_hash != revision.source_hash:
                    raise ValueError("fact source mismatch")
        wanted = [r.id for r, *_ in items] + [r.supersedes for r, *_ in items if r.supersedes]
        with self.connection(scope) as c:
            self.lock(c, scope.tenant_id)
            known = {
                row["id"]: row["document_id"]
                for row in c.execute("SELECT id,document_id FROM fw_revisions WHERE id=ANY(%s)", (wanted,)).fetchall()
            }
            new = []
            for revision, payload, facts, assets in items:
                if revision.id in known:
                    continue
                if revision.supersedes and known.get(revision.supersedes) != revision.document_id:
                    raise ValueError("supersedes must refer to an existing revision of this document")
                known[revision.id] = revision.document_id
                new.append((revision, payload, facts, assets))
            if new:
                with c.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO fw_revisions VALUES (%s,%s,%s,%s,%s)",
                        [(scope.tenant_id, r.id, r.document_id, r.supersedes, Jsonb(r.model_dump(mode="json")))
                         for r, *_ in new])
                    cur.executemany(
                        "INSERT INTO fw_assets VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        [(scope.tenant_id, r.id, kind, blob) for r, payload, _, assets in new
                         for kind, blob in [("pdf", payload), *assets.items()]])
                    rows = [(scope.tenant_id, r.id, f.id, Jsonb(f.model_dump(mode="json"))) for r, _, facts, _ in new for f in facts]
                    if rows:
                        cur.executemany("INSERT INTO fw_facts VALUES (%s,%s,%s,%s)", rows)
        return len(new)

    def seed_claim_case(self, scope, case: dict, documents: list[tuple[dict, bytes, bytes | None]], prior_keys: list[str]) -> bool:
        """Case row, its documents and any pre-paid ledger keys in one transaction."""
        return self.seed_claim_cases(scope, [(case, documents, prior_keys)]) == 1

    def seed_claim_cases(self, scope, items: list[tuple[dict, list[tuple[dict, bytes, bytes | None]], list[str]]]) -> int:
        """Several claim cases in one transaction; existing cases are skipped. Returns how many were new."""
        if scope.role == "viewer":
            raise PermissionError("read only")
        with self.connection(scope) as c:
            existing = {
                row["case_id"] for row in c.execute(
                    "SELECT case_id FROM fw_claim_cases WHERE case_id=ANY(%s)", ([case["case_id"] for case, _, _ in items],)
                ).fetchall()
            }
            new = [(case, docs, keys) for case, docs, keys in items if case["case_id"] not in existing]
            if new:
                with c.cursor() as cur:
                    cur.executemany("INSERT INTO fw_claim_cases (tenant_id,case_id,data) VALUES (%s,%s,%s)",
                                    [(scope.tenant_id, case["case_id"], Jsonb(case)) for case, _, _ in new])
                    cur.executemany(
                        "INSERT INTO fw_claim_docs (tenant_id,id,case_id,kind,issued_at,pdf,png) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        [(scope.tenant_id, d["id"], d["case_id"], d["kind"], d["issued_at"], pdf, png)
                         for _, docs, _ in new for d, pdf, png in docs])
                    keys = [(scope.tenant_id, key, "PRIOR", case["policy"]["policy_id"]) for case, _, prior in new for key in prior]
                    if keys:
                        cur.executemany(
                            "INSERT INTO fw_payout_keys (tenant_id,dedupe_key,claim_id,policy_id) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                            keys)
        return len(new)

    def documents(self, scope) -> list[dict]:
        """Every revision with its facts and active flag, in one connection (the workbench's first call)."""
        with self.connection(scope) as c:
            snapshot, revisions, fact_rows = self.corpus_in(c)
        by_rev: dict[str, list] = {}
        for f in fact_rows:
            by_rev.setdefault(f["revision_id"], []).append(f["data"])
        active = set(snapshot.revision_ids)
        return [{**r.model_dump(mode="json"), "active": r.id in active, "facts": by_rev.get(r.id, [])} for r in revisions]

    def corpus(self, scope):
        """The current snapshot, its active revisions and their facts, in one connection.

        Search and a run's tools used to load these in three or four separate connections
        (a snapshot for the staleness check, the revisions, the facts); on a remote database
        each connection is a round trip or two."""
        with self.connection(scope) as c:
            snapshot, revisions, fact_rows = self.corpus_in(c)
        active = set(snapshot.revision_ids)
        revisions = [r for r in revisions if r.id in active]
        facts: dict[str, list[Fact]] = {r.id: [] for r in revisions}
        for row in fact_rows:
            if row["revision_id"] in facts:
                facts[row["revision_id"]].append(Fact.model_validate(row["data"]))
        return snapshot, revisions, facts

    def get_revision(self, scope, revision_id):
        with self.connection(scope) as c:
            r = c.execute(
                "SELECT data FROM fw_revisions WHERE id=%s", (revision_id,)
            ).fetchone()
        return DrawingRevision.model_validate(r["data"]) if r else None

    def list_revisions(self, scope):
        with self.connection(scope) as c:
            rs = c.execute("SELECT data FROM fw_revisions ORDER BY id").fetchall()
        return [DrawingRevision.model_validate(r["data"]) for r in rs]

    def put_asset(self, scope, revision_id, kind, payload):
        if scope.role == "viewer":
            raise PermissionError("read only")
        with self.connection(scope) as c:
            c.execute(
                "INSERT INTO fw_assets VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (scope.tenant_id, revision_id, kind, payload),
            )

    def asset(self, scope, revision_id, kind):
        with self.connection(scope) as c:
            r = c.execute(
                "SELECT data FROM fw_assets WHERE revision_id=%s AND kind=%s",
                (revision_id, kind),
            ).fetchone()
        return bytes(r["data"]) if r else None

    def save_facts(self, scope, revision_id, facts: list[Fact]):
        if scope.role == "viewer":
            raise PermissionError("read only")
        rev = self.get_revision(scope, revision_id)
        if not rev:
            raise ValueError("revision not found")
        for f in facts:
            if (
                f.source.revision_id != revision_id
                or f.source.source_hash != rev.source_hash
            ):
                raise ValueError("fact source mismatch")
        with self.connection(scope) as c:
            self.lock(c, scope.tenant_id)
            c.execute("DELETE FROM fw_facts WHERE revision_id=%s", (revision_id,))
            c.execute("DELETE FROM fw_vectors WHERE revision_id=%s", (revision_id,))
            for f in facts:
                c.execute(
                    "INSERT INTO fw_facts VALUES (%s,%s,%s,%s)",
                    (
                        scope.tenant_id,
                        revision_id,
                        f.id,
                        Jsonb(f.model_dump(mode="json")),
                    ),
                )

    def load_facts(self, scope, revision_id):
        with self.connection(scope) as c:
            rs = c.execute(
                "SELECT data FROM fw_facts WHERE revision_id=%s ORDER BY id",
                (revision_id,),
            ).fetchall()
        return [Fact.model_validate(r["data"]) for r in rs]

    MAX_SAVED_TOOLS = 20

    def save_tool(self, scope, name, definition: dict, run_id=None):
        """Store an agent-defined search tool for this tenant; redefining a name replaces it."""
        with self.connection(scope) as c:
            self.lock(c, scope.tenant_id)
            used = c.execute("SELECT count(*) AS n FROM fw_saved_tools WHERE name<>%s", (name,)).fetchone()["n"]
            if used >= self.MAX_SAVED_TOOLS:
                raise ValueError(f"saved tool limit reached ({self.MAX_SAVED_TOOLS})")
            c.execute(
                "INSERT INTO fw_saved_tools (tenant_id,name,definition,created_by_run) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT(tenant_id,name) DO UPDATE SET definition=excluded.definition,created_by_run=excluded.created_by_run,created_at=now()",
                (scope.tenant_id, name, Jsonb(definition), run_id),
            )

    def saved_tools(self, scope):
        with self.connection(scope) as c:
            rows = c.execute("SELECT name,definition,created_by_run,created_at FROM fw_saved_tools ORDER BY name").fetchall()
        return [{"name": r["name"], **r["definition"], "created_by_run": r["created_by_run"],
                 "created_at": r["created_at"].isoformat()} for r in rows]

    def saved_tool(self, scope, name):
        with self.connection(scope) as c:
            row = c.execute("SELECT definition FROM fw_saved_tools WHERE name=%s", (name,)).fetchone()
        return row["definition"] if row else None

    # -- claims: cases, documents, ledger ------------------------------------------------
    def save_claim_case(self, scope, case: dict) -> None:
        with self.connection(scope) as c:
            c.execute(
                "INSERT INTO fw_claim_cases (tenant_id,case_id,data) VALUES (%s,%s,%s) ON CONFLICT(tenant_id,case_id) DO UPDATE SET data=excluded.data",
                (scope.tenant_id, case["case_id"], Jsonb(case)),
            )

    def claim_cases(self, scope) -> list[dict]:
        with self.connection(scope) as c:
            rows = c.execute("SELECT data FROM fw_claim_cases ORDER BY case_id").fetchall()
        return [r["data"] for r in rows]

    def claim_case(self, scope, case_id) -> dict | None:
        with self.connection(scope) as c:
            row = c.execute("SELECT data FROM fw_claim_cases WHERE case_id=%s", (case_id,)).fetchone()
        return row["data"] if row else None

    def save_claim_doc(self, scope, doc: dict, pdf: bytes, png: bytes | None) -> None:
        with self.connection(scope) as c:
            c.execute(
                "INSERT INTO fw_claim_docs (tenant_id,id,case_id,kind,issued_at,pdf,png) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (scope.tenant_id, doc["id"], doc["case_id"], doc["kind"], doc["issued_at"], pdf, png),
            )

    def claim_doc(self, scope, doc_id):
        """(meta dict, pdf bytes, png bytes) or None."""
        with self.connection(scope) as c:
            row = c.execute("SELECT id,case_id,kind,issued_at,pdf,png FROM fw_claim_docs WHERE id=%s", (doc_id,)).fetchone()
        if not row:
            return None
        meta = {k: row[k] for k in ("id", "case_id", "kind", "issued_at")}
        return meta, bytes(row["pdf"]), (bytes(row["png"]) if row["png"] is not None else None)

    def pay(self, scope, claim_id, policy_id, amount, dedupe_keys, line_items, run_id=None) -> dict:
        """Record a payout once. Returns status 'paid' the first time and 'already_paid' afterwards."""
        with self.connection(scope) as c:
            self.lock(c, scope.tenant_id)
            inserted = c.execute(
                "INSERT INTO fw_payouts (tenant_id,claim_id,policy_id,amount,line_items,run_id) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (tenant_id,claim_id) DO NOTHING RETURNING claim_id",
                (scope.tenant_id, claim_id, policy_id, amount, Jsonb(line_items), run_id),
            ).fetchone()
            if inserted:
                for key in dedupe_keys:
                    c.execute(
                        "INSERT INTO fw_payout_keys (tenant_id,dedupe_key,claim_id,policy_id) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (scope.tenant_id, key, claim_id, policy_id),
                    )
            row = c.execute("SELECT amount,paid_at,run_id FROM fw_payouts WHERE claim_id=%s", (claim_id,)).fetchone()
        return {"status": "paid" if inserted else "already_paid", "claim_id": claim_id, "amount": int(row["amount"]),
                "paid_at": row["paid_at"].isoformat(), "run_id": row["run_id"]}

    def paid_keys(self, scope, policy_id) -> set[str]:
        with self.connection(scope) as c:
            rows = c.execute("SELECT dedupe_key FROM fw_payout_keys WHERE policy_id=%s", (policy_id,)).fetchall()
        return {r["dedupe_key"] for r in rows}

    def seed_paid_keys(self, scope, policy_id, keys, claim_id="PRIOR") -> None:
        """Pretend earlier claims were paid (the duplicate scenario of the synthetic data)."""
        with self.connection(scope) as c:
            for key in keys:
                c.execute(
                    "INSERT INTO fw_payout_keys (tenant_id,dedupe_key,claim_id,policy_id) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (scope.tenant_id, key, claim_id, policy_id),
                )

    def payouts(self, scope) -> list[dict]:
        with self.connection(scope) as c:
            rows = c.execute("SELECT claim_id,policy_id,amount,line_items,run_id,paid_at FROM fw_payouts ORDER BY paid_at DESC").fetchall()
        return [{**{k: r[k] for k in ("claim_id", "policy_id", "line_items", "run_id")}, "amount": int(r["amount"]),
                 "paid_at": r["paid_at"].isoformat()} for r in rows]

    def load_facts_many(self, scope, revision_ids):
        result = {rid: [] for rid in revision_ids}
        with self.connection(scope) as c:
            rows = c.execute('SELECT revision_id,data FROM fw_facts WHERE revision_id=ANY(%s) ORDER BY id',
                             (revision_ids,)).fetchall()
        for row in rows:
            result[row['revision_id']].append(Fact.model_validate(row['data']))
        return result

    @staticmethod
    def active(revisions):
        groups = {}
        for r in revisions:
            if r.approval != "draft" and datetime.fromisoformat(
                r.effective_from
            ) <= datetime.now(timezone.utc):
                groups.setdefault(r.document_id, []).append(r)
        active = []
        conflicts = []
        for doc, rs in groups.items():
            superseded = {r.supersedes for r in rs}
            leaves = [r for r in rs if r.id not in superseded]
            if len(leaves) == 1 and leaves[0].approval == "approved":
                active.append(leaves[0].id)
            elif len(leaves) > 1:
                conflicts.append(doc)
        return sorted(active), sorted(conflicts)

    def revision_conflicts(self, scope):
        return self.active(self.list_revisions(scope))[1]

    def snapshot(self, scope):
        with self.connection(scope) as c:
            return self.snapshot_in(c, scope)

    def snapshot_in(self, c, scope):
        return self.corpus_in(c)[0]

    def corpus_in(self, c):
        """Revisions, facts and vector digests of the current tenant on an open connection.

        The three reads are queued before the first fetch, so in pipeline mode they travel in
        one round trip. The snapshot digest is computed over the same rows as before."""
        revision_q = c.execute("SELECT data FROM fw_revisions ORDER BY id")
        fact_q = c.execute("SELECT revision_id,data FROM fw_facts ORDER BY id")
        vector_q = c.execute(
            "SELECT revision_id,channel,md5(embedding::text) AS hash,metadata FROM fw_vectors ORDER BY revision_id,channel"
        )
        rows, fact_rows, vectors = revision_q.fetchall(), fact_q.fetchall(), vector_q.fetchall()
        revisions = [DrawingRevision.model_validate(r["data"]) for r in rows]
        ids, _ = self.active(revisions)
        digest = sha256(
            json.dumps(
                {
                    "revisions": rows,
                    "facts": [{"data": f["data"]} for f in fact_rows],
                    "vectors": vectors,
                    "active_ids": ids,
                },
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        return SearchSnapshot(id=digest, revision_ids=ids, index_hash=digest), revisions, fact_rows

    def save_vector(self, scope, revision_id, channel, vector):
        if scope.role == "viewer":
            raise PermissionError("read only")
        import math

        if not vector or not all(math.isfinite(v) for v in vector):
            raise ValueError("invalid vector")
        with self.connection(scope) as c:
            self.lock(c, scope.tenant_id)
            c.execute(
                "INSERT INTO fw_vectors (tenant_id,revision_id,channel,embedding) VALUES(%s,%s,%s,%s::vector) ON CONFLICT(tenant_id,revision_id,channel) DO UPDATE SET embedding=excluded.embedding,metadata='{}'::jsonb",
                (scope.tenant_id, revision_id, channel, str(vector)),
            )

    def save_index(self, scope, revision_id, vectors, metadata):
        from fitwitness.retrieval.indexing import document_text, source_metadata
        import math
        if scope.role == 'viewer':
            raise PermissionError('read only')
        if set(vectors) != {'text', 'image'} or any(
            not v or not all(math.isfinite(x) for x in v) or sum(x*x for x in v) == 0
            for v in vectors.values()
        ):
            raise ValueError('invalid vector index')
        with self.connection(scope) as c:
            self.lock(c, scope.tenant_id)
            row = c.execute('SELECT data FROM fw_revisions WHERE id=%s', (revision_id,)).fetchone()
            if not row:
                raise ValueError('revision not found')
            revision = DrawingRevision.model_validate(row['data'])
            facts = [Fact.model_validate(r['data']) for r in c.execute(
                'SELECT data FROM fw_facts WHERE revision_id=%s ORDER BY id', (revision_id,))]
            image = c.execute("SELECT data FROM fw_assets WHERE revision_id=%s AND kind='png'", (revision_id,)).fetchone()
            if not image or metadata != source_metadata(revision, document_text(revision, facts),
                                                       bytes(image['data']), metadata.get('encoder_fingerprint')):
                raise ValueError('index source changed during encoding')
            for channel, vector in vectors.items():
                c.execute('INSERT INTO fw_vectors (tenant_id,revision_id,channel,embedding,metadata) '
                          'VALUES(%s,%s,%s,%s::vector,%s) ON CONFLICT(tenant_id,revision_id,channel) '
                          'DO UPDATE SET embedding=excluded.embedding,metadata=excluded.metadata',
                          (scope.tenant_id, revision_id, channel, str(vector), Jsonb(metadata)))

    def vector_metadata(self, scope, revision_ids):
        with self.connection(scope) as c:
            rows = c.execute('SELECT revision_id,channel,metadata FROM fw_vectors WHERE revision_id=ANY(%s)',
                             (revision_ids,)).fetchall()
        result = {}
        for r in rows:
            result.setdefault(r['revision_id'], {})[r['channel']] = r['metadata']
        return result

    def vector_search(self, scope, channel, vector, revision_ids, top_k):
        with self.connection(scope) as c:
            return c.execute(
                "SELECT revision_id,embedding <=> %s::vector AS distance FROM fw_vectors WHERE channel=%s AND revision_id=ANY(%s) ORDER BY distance,revision_id LIMIT %s",
                (str(vector), channel, revision_ids, top_k),
            ).fetchall()
