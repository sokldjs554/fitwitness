"""PostgreSQL repository. All data reads use a transaction-scoped RLS role."""

from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import threading
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

# One pool per DSN per process. Every request-scoped operation used to open its own TLS
# connection; against a remote database that cost more than the query itself.
_POOLS: dict[str, ConnectionPool] = {}
_POOLS_LOCK = threading.Lock()


def pool_for(dsn: str) -> ConnectionPool:
    with _POOLS_LOCK:
        pool = _POOLS.get(dsn)
        if pool is None or pool.closed:
            pool = ConnectionPool(
                dsn,
                min_size=1,
                max_size=int(os.getenv("FITWITNESS_DB_POOL_MAX", "8")),
                max_idle=120,
                kwargs={"row_factory": dict_row},
                check=ConnectionPool.check_connection,
                open=True,
                name=f"fitwitness-{len(_POOLS)}",
            )
            _POOLS[dsn] = pool
        return pool
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
        """Open the pool now (an idle worker does this before a job arrives)."""
        pool_for(self.dsn)

    @contextmanager
    def connection(self, scope: TenantScope):
        """A pooled connection inside one transaction, as the non-bypass role, scoped to the tenant.

        Both settings are transaction-local, so they vanish when the pool commits or rolls back
        at exit; the next borrower starts from the owner role with no tenant."""
        with pool_for(self.dsn).connection() as c:
            c.execute(
                "SELECT set_config('role','fitwitness_app',true), set_config('app.tenant',%s,true)",
                (scope.tenant_id,),
            )
            yield c

    @contextmanager
    def plain(self):
        """A pooled connection as the owner role, for tables without row-level security."""
        with pool_for(self.dsn).connection() as c:
            yield c

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
        if scope.role == "viewer" or revision.tenant_id != scope.tenant_id:
            raise PermissionError("write scope mismatch")
        if sha256(payload).hexdigest() != revision.source_hash:
            raise ValueError("source hash mismatch")
        for f in facts:
            if f.source.revision_id != revision.id or f.source.source_hash != revision.source_hash:
                raise ValueError("fact source mismatch")
        with self.connection(scope) as c:
            self.lock(c, scope.tenant_id)
            if c.execute("SELECT 1 FROM fw_revisions WHERE id=%s", (revision.id,)).fetchone():
                return False
            if revision.supersedes:
                parent = c.execute("SELECT document_id FROM fw_revisions WHERE id=%s", (revision.supersedes,)).fetchone()
                if not parent or parent["document_id"] != revision.document_id:
                    raise ValueError("supersedes must refer to an existing revision of this document")
            c.execute("INSERT INTO fw_revisions VALUES (%s,%s,%s,%s,%s)",
                      (scope.tenant_id, revision.id, revision.document_id, revision.supersedes, Jsonb(revision.model_dump(mode="json"))))
            with c.cursor() as cur:
                cur.executemany("INSERT INTO fw_assets VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                                [(scope.tenant_id, revision.id, "pdf", payload)] + [(scope.tenant_id, revision.id, k, v) for k, v in assets.items()])
                cur.executemany("INSERT INTO fw_facts VALUES (%s,%s,%s,%s)",
                                [(scope.tenant_id, revision.id, f.id, Jsonb(f.model_dump(mode="json"))) for f in facts])
        return True

    def seed_claim_case(self, scope, case: dict, documents: list[tuple[dict, bytes, bytes | None]], prior_keys: list[str]) -> bool:
        """Case row, its documents and any pre-paid ledger keys in one transaction."""
        with self.connection(scope) as c:
            if c.execute("SELECT 1 FROM fw_claim_cases WHERE case_id=%s", (case["case_id"],)).fetchone():
                return False
            c.execute("INSERT INTO fw_claim_cases (tenant_id,case_id,data) VALUES (%s,%s,%s)", (scope.tenant_id, case["case_id"], Jsonb(case)))
            with c.cursor() as cur:
                cur.executemany(
                    "INSERT INTO fw_claim_docs (tenant_id,id,case_id,kind,issued_at,pdf,png) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    [(scope.tenant_id, d["id"], d["case_id"], d["kind"], d["issued_at"], pdf, png) for d, pdf, png in documents])
                if prior_keys:
                    cur.executemany(
                        "INSERT INTO fw_payout_keys (tenant_id,dedupe_key,claim_id,policy_id) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        [(scope.tenant_id, key, "PRIOR", case["policy"]["policy_id"]) for key in prior_keys])
        return True

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
        rows = c.execute("SELECT data FROM fw_revisions ORDER BY id").fetchall()
        facts = c.execute("SELECT data FROM fw_facts ORDER BY id").fetchall()
        revisions = [DrawingRevision.model_validate(r["data"]) for r in rows]
        ids, _ = self.active(revisions)
        vectors = c.execute(
            "SELECT revision_id,channel,md5(embedding::text) AS hash,metadata FROM fw_vectors ORDER BY revision_id,channel"
        ).fetchall()
        digest = sha256(
            json.dumps(
                {
                    "revisions": rows,
                    "facts": facts,
                    "vectors": vectors,
                    "active_ids": ids,
                },
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        return SearchSnapshot(id=digest, revision_ids=ids, index_hash=digest)

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
