"""PostgreSQL repository. All data reads use a transaction-scoped RLS role."""

from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
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
            c.execute(
                "CREATE TABLE IF NOT EXISTS fw_vectors (tenant_id text NOT NULL, revision_id text NOT NULL, channel text NOT NULL, embedding vector NOT NULL, PRIMARY KEY(tenant_id,revision_id,channel), FOREIGN KEY(tenant_id,revision_id) REFERENCES fw_revisions(tenant_id,id))"
            )
            for name in ["fw_revisions", "fw_assets", "fw_facts", "fw_vectors"]:
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

    @contextmanager
    def connection(self, scope: TenantScope):
        with psycopg.connect(self.dsn, row_factory=dict_row) as c:
            c.execute("SET LOCAL ROLE fitwitness_app")
            c.execute("SELECT set_config('app.tenant',%s,true)", (scope.tenant_id,))
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
            "SELECT revision_id,channel,md5(embedding::text) AS hash FROM fw_vectors ORDER BY revision_id,channel"
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
                "INSERT INTO fw_vectors VALUES(%s,%s,%s,%s::vector) ON CONFLICT(tenant_id,revision_id,channel) DO UPDATE SET embedding=excluded.embedding",
                (scope.tenant_id, revision_id, channel, str(vector)),
            )

    def vector_search(self, scope, channel, vector, revision_ids, top_k):
        with self.connection(scope) as c:
            return c.execute(
                "SELECT revision_id,embedding <=> %s::vector AS distance FROM fw_vectors WHERE channel=%s AND revision_id=ANY(%s) ORDER BY distance,revision_id LIMIT %s",
                (str(vector), channel, revision_ids, top_k),
            ).fetchall()
