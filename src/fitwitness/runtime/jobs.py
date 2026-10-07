"""Durable job, lease and event storage with atomic snapshot fencing."""

from hashlib import sha256
import json
from uuid import uuid4
import psycopg
from psycopg.types.json import Jsonb
from fitwitness.contracts import RunRequest, RunView, Usage, Decision, uid, now


def backoff_seconds(attempt: int, base: int = 5, cap: int = 300) -> int:
    """Exponential delay before retry number ``attempt`` (0-based), capped."""
    return int(min(cap, base * 2**attempt))


class Jobs:
    MAX_ATTEMPTS = 3

    def __init__(self, repo):
        self.repo = repo

    def migrate(self):
        with psycopg.connect(self.repo.dsn, autocommit=True) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS fw_runs (
                tenant_id text NOT NULL,id text NOT NULL,idempotency_key text NOT NULL,input_hash text NOT NULL,
                request jsonb NOT NULL,snapshot_id text NOT NULL,state text NOT NULL DEFAULT 'queued',
                result jsonb NOT NULL DEFAULT '[]',usage jsonb NOT NULL DEFAULT '{}',error text,
                lease_token text,lease_until timestamptz,fault_consumed bool NOT NULL DEFAULT false,
                created_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,idempotency_key));
                CREATE TABLE IF NOT EXISTS fw_events (
                tenant_id text NOT NULL,run_id text NOT NULL,seq integer NOT NULL,kind text NOT NULL,payload jsonb NOT NULL,
                timestamp timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,run_id,seq),
                FOREIGN KEY(tenant_id,run_id) REFERENCES fw_runs(tenant_id,id));""")
            for column, ddl in [
                ("human_input", "jsonb"),
                ("question", "text"),
                ("attempts", "integer NOT NULL DEFAULT 0"),
                ("next_attempt_at", "timestamptz"),
                ("paused_since", "timestamptz"),
                ("waited_seconds", "double precision NOT NULL DEFAULT 0"),
            ]:
                c.execute(f"ALTER TABLE fw_runs ADD COLUMN IF NOT EXISTS {column} {ddl}")
            c.execute(
                "CREATE TABLE IF NOT EXISTS fw_dispatch (tenant_id text NOT NULL,run_id text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,run_id))"
            )
            c.execute("ALTER TABLE fw_dispatch ENABLE ROW LEVEL SECURITY")
            if not c.execute(
                "SELECT 1 FROM pg_policies WHERE tablename='fw_dispatch' AND policyname='tenant_scope'"
            ).fetchone():
                c.execute(
                    "CREATE POLICY tenant_scope ON fw_dispatch USING (tenant_id=current_setting('app.tenant',true)) WITH CHECK (tenant_id=current_setting('app.tenant',true))"
                )
            c.execute("GRANT SELECT,INSERT,DELETE ON fw_dispatch TO fitwitness_app")
            c.execute(
                "CREATE TABLE IF NOT EXISTS fw_admission (bucket text PRIMARY KEY,used integer NOT NULL,created_at timestamptz NOT NULL DEFAULT now())"
            )
            for name in ["fw_runs", "fw_events"]:
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

    def enqueue(self, scope, request: RunRequest, idempotency_key: str):
        if scope.role == "viewer":
            raise PermissionError("read only")
        body = request.model_dump(mode="json")
        digest = sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        with self.repo.connection(scope) as c:
            self.repo.lock(c, scope.tenant_id)
            row = c.execute(
                "SELECT * FROM fw_runs WHERE idempotency_key=%s", (idempotency_key,)
            ).fetchone()
            if row:
                if row["input_hash"] != digest:
                    raise ValueError("idempotency key reused with a different request")
                run_id = row["id"]
            else:
                count = c.execute(
                    "SELECT count(*) AS n FROM fw_runs WHERE state IN ('queued','running','retry_wait')"
                ).fetchone()["n"]
                if count >= 3:
                    raise ValueError(
                        "동시 실행 제한: 기존 실행이 끝난 후 다시 시도해 주세요"
                    )
                run_id = uid()
                snapshot = self.repo.snapshot_in(c, scope)
                c.execute(
                    "INSERT INTO fw_runs(tenant_id,id,idempotency_key,input_hash,request,snapshot_id) VALUES(%s,%s,%s,%s,%s,%s)",
                    (
                        scope.tenant_id,
                        run_id,
                        idempotency_key,
                        digest,
                        Jsonb(body),
                        snapshot.id,
                    ),
                )
                c.execute(
                    "INSERT INTO fw_dispatch(tenant_id,run_id) VALUES(%s,%s)",
                    (scope.tenant_id, run_id),
                )
                self._event(
                    c,
                    scope,
                    run_id,
                    "queued",
                    {"provider": request.provider, "mode": request.mode},
                )
        return self.get(scope, run_id)

    def list_runs(self, scope, limit=200):
        with self.repo.connection(scope) as c:
            return c.execute("SELECT id,state,request,created_at FROM fw_runs ORDER BY created_at DESC LIMIT %s", (limit,)).fetchall()

    def raw(self, scope, run_id):
        with self.repo.connection(scope) as c:
            return c.execute("SELECT * FROM fw_runs WHERE id=%s", (run_id,)).fetchone()

    def get(self, scope, run_id):
        r = self.raw(scope, run_id)
        if not r:
            return None
        if (
            r["state"] == "completed"
            and r["request"].get("kind") != "claim"
            and self.repo.snapshot(scope).id != r["snapshot_id"]
        ):
            self.invalidate(scope)
            r = self.raw(scope, run_id)
        is_claim = r["request"].get("kind") == "claim"
        return RunView(
            id=r["id"],
            state=r["state"],
            kind="claim" if is_claim else "drawing",
            decisions=[] if is_claim else [Decision.model_validate(d) for d in r["result"]],
            claim=(r["result"] if is_claim and isinstance(r["result"], dict) else None),
            usage=Usage.model_validate(r["usage"]),
            error=r["error"],
            input_hash=r["input_hash"],
            snapshot_id=r["snapshot_id"],
            provider=r["request"]["provider"],
            model_id=r["request"]["model_id"],
            question=r["question"],
            attempts=r["attempts"],
            next_attempt_at=r["next_attempt_at"].isoformat() if r["next_attempt_at"] else None,
        )

    def claim(self, scope, run_id, lease_seconds=30, requested_token=None):
        with self.repo.connection(scope) as c:
            r = c.execute(
                "SELECT * FROM fw_runs WHERE id=%s FOR UPDATE", (run_id,)
            ).fetchone()
            if not r or r["state"] not in ["queued", "running", "retry_wait"]:
                return None
            if (
                r["lease_token"]
                and c.execute(
                    "SELECT %s > now() AS active", (r["lease_until"],)
                ).fetchone()["active"]
            ):
                return None
            if (
                r["state"] == "retry_wait"
                and r["next_attempt_at"] is not None
                and not c.execute("SELECT %s <= now() AS due", (r["next_attempt_at"],)).fetchone()["due"]
            ):
                return None
            token = requested_token or uid()
            # Time spent waiting for a retry or a reviewer does not count against the run's deadline.
            c.execute(
                "UPDATE fw_runs SET state='running',lease_token=%s,lease_until=now()+(%s * interval '1 second'),"
                "waited_seconds=waited_seconds+COALESCE(EXTRACT(EPOCH FROM (now()-paused_since)),0),paused_since=NULL,next_attempt_at=NULL WHERE id=%s",
                (token, lease_seconds, run_id),
            )
            self._event(
                c, scope, run_id, "started", {"recovered": r["state"] == "running"}
            )
            return token

    def heartbeat(self, scope, run_id, token):
        with self.repo.connection(scope) as c:
            return bool(
                c.execute(
                    "UPDATE fw_runs SET lease_until=now()+interval '30 seconds' WHERE id=%s AND lease_token=%s AND state='running' AND lease_until>now() RETURNING id",
                    (run_id, token),
                ).fetchone()
            )

    def _event(self, c, scope, run_id, kind, payload):
        c.execute("SELECT id FROM fw_runs WHERE id=%s FOR UPDATE", (run_id,))
        seq = c.execute(
            "SELECT COALESCE(MAX(seq),0)+1 AS n FROM fw_events WHERE run_id=%s",
            (run_id,),
        ).fetchone()["n"]
        c.execute(
            "INSERT INTO fw_events(tenant_id,run_id,seq,kind,payload) VALUES(%s,%s,%s,%s,%s)",
            (scope.tenant_id, run_id, seq, kind, Jsonb(payload)),
        )

    def event(self, scope, run_id, kind, payload, token=None):
        with self.repo.connection(scope) as c:
            if token is not None:
                row = c.execute(
                    "SELECT id FROM fw_runs WHERE id=%s AND lease_token=%s AND state='running' AND lease_until>now() FOR UPDATE",
                    (run_id, token),
                ).fetchone()
                if not row:
                    raise RuntimeError("실행 소유권 만료")
            self._event(c, scope, run_id, kind, payload)

    def events(self, scope, run_id, after=0):
        with self.repo.connection(scope) as c:
            return c.execute(
                "SELECT run_id,seq,kind,payload,timestamp FROM fw_events WHERE run_id=%s AND seq>%s ORDER BY seq LIMIT 500",
                (run_id, after),
            ).fetchall()

    def finalize(self, scope, run_id, snapshot_id, decisions, lease_token, usage=None):
        with self.repo.connection(scope) as c:
            self.repo.lock(c, scope.tenant_id)
            r = c.execute(
                "SELECT * FROM fw_runs WHERE id=%s FOR UPDATE", (run_id,)
            ).fetchone()
            if not r or r["state"] != "running" or r["lease_token"] != lease_token:
                return False
            if not c.execute(
                "SELECT %s>now() AS active", (r["lease_until"],)
            ).fetchone()["active"]:
                return False
            if self.repo.snapshot_in(c, scope).id != snapshot_id:
                c.execute(
                    "UPDATE fw_runs SET state='stale',lease_token=NULL WHERE id=%s",
                    (run_id,),
                )
                self._event(
                    c,
                    scope,
                    run_id,
                    "stale",
                    {"reason": "도면 또는 추출 근거가 변경되어 재검증이 필요합니다"},
                )
                return False
            c.execute(
                "UPDATE fw_runs SET state='completed',result=%s,usage=%s,lease_token=NULL WHERE id=%s",
                (
                    Jsonb([d.model_dump(mode="json") for d in decisions]),
                    Jsonb((usage or Usage()).model_dump(mode="json")),
                    run_id,
                ),
            )
            c.execute("DELETE FROM fw_dispatch WHERE run_id=%s", (run_id,))
            self._event(c, scope, run_id, "completed", {"candidates": len(decisions)})
            return True

    def cancel(self, scope, run_id):
        if scope.role == "viewer":
            raise PermissionError("read only")
        with self.repo.connection(scope) as c:
            r = c.execute(
                "UPDATE fw_runs SET state='cancelled',lease_token=NULL WHERE id=%s AND state IN ('queued','running','retry_wait','waiting_input') RETURNING id",
                (run_id,),
            ).fetchone()
            if r:
                self._event(c, scope, run_id, "cancelled", {})
        return self.get(scope, run_id)

    def fail(self, scope, run_id, token, error, *, retryable=False, max_attempts=None):
        """Terminal failure, or a scheduled retry when the cause is transient.

        A retryable failure parks the run in ``retry_wait`` with exponential backoff and
        keeps it on the dispatch queue; once ``max_attempts`` is reached it is dead-lettered
        (state ``failed``, event ``dead_lettered``) so the operator can see it was not a
        one-off error."""
        max_attempts = max_attempts or self.MAX_ATTEMPTS
        with self.repo.connection(scope) as c:
            r = c.execute(
                "SELECT attempts FROM fw_runs WHERE id=%s AND lease_token=%s AND state='running' FOR UPDATE",
                (run_id, token),
            ).fetchone()
            if not r:
                return
            attempt = r["attempts"] + 1
            if retryable and attempt < max_attempts:
                delay = backoff_seconds(attempt - 1)
                c.execute(
                    "UPDATE fw_runs SET state='retry_wait',attempts=%s,error=%s,lease_token=NULL,lease_until=NULL,"
                    "next_attempt_at=now()+(%s * interval '1 second'),paused_since=now() WHERE id=%s",
                    (attempt, error[:500], delay, run_id),
                )
                self._event(c, scope, run_id, "retry_scheduled", {"attempt": attempt, "max_attempts": max_attempts, "delay_seconds": delay, "error": error[:500]})
                return
            c.execute(
                "UPDATE fw_runs SET state='failed',attempts=%s,error=%s,lease_token=NULL WHERE id=%s",
                (attempt, error[:500], run_id),
            )
            c.execute("DELETE FROM fw_dispatch WHERE run_id=%s", (run_id,))
            self._event(c, scope, run_id, "dead_lettered" if retryable else "failed", {"error": error[:500], "attempts": attempt})

    def finalize_result(self, scope, run_id, result: dict, lease_token, usage=None):
        """Complete a run whose result is one object (a claim outcome) rather than drawing decisions."""
        with self.repo.connection(scope) as c:
            r = c.execute("SELECT * FROM fw_runs WHERE id=%s FOR UPDATE", (run_id,)).fetchone()
            if not r or r["state"] != "running" or r["lease_token"] != lease_token:
                return False
            if not c.execute("SELECT %s>now() AS active", (r["lease_until"],)).fetchone()["active"]:
                return False
            c.execute(
                "UPDATE fw_runs SET state='completed',result=%s,lease_token=NULL,usage=COALESCE(%s,usage) WHERE id=%s",
                (Jsonb(result), Jsonb(usage.model_dump(mode="json")) if usage else None, run_id),
            )
            c.execute("DELETE FROM fw_dispatch WHERE run_id=%s", (run_id,))
            self._event(c, scope, run_id, "completed", {"outcome": result.get("decision", {}).get("outcome")})
            return True

    def make_due(self, scope, run_id):
        """Operator action: run a backed-off retry now."""
        with self.repo.connection(scope) as c:
            c.execute("UPDATE fw_runs SET next_attempt_at=now() WHERE id=%s AND state='retry_wait'", (run_id,))

    def wait_input(self, scope, run_id, token, question, payload):
        """Park a running job until a reviewer answers; it leaves the dispatch queue meanwhile.

        The lease is released as well: the dispatcher skips any run whose lease is still in
        the future, so a parked run that kept its expiry would wait out the full lease
        before the reviewer's answer could be picked up."""
        with self.repo.connection(scope) as c:
            row = c.execute(
                "UPDATE fw_runs SET state='waiting_input',question=%s,lease_token=NULL,lease_until=NULL,paused_since=now() "
                "WHERE id=%s AND lease_token=%s AND state='running' RETURNING id",
                (question[:500], run_id, token),
            ).fetchone()
            if not row:
                return False
            c.execute("DELETE FROM fw_dispatch WHERE run_id=%s", (run_id,))
            self._event(c, scope, run_id, "waiting_input", payload)
            return True

    def resume(self, scope, run_id, human_input):
        """Store the reviewer's answer and queue the run again; the worker resumes the graph with it."""
        if scope.role == "viewer":
            raise PermissionError("read only")
        current = self.raw(scope, run_id)
        if not current or current["state"] != "waiting_input":
            raise ValueError("실행이 담당자 검토 대기 상태가 아닙니다")
        # Validate the answer against the shape this kind of run resumes with, before it is stored:
        # a malformed answer must fail here, not inside the worker.
        if current["request"].get("kind") == "claim":
            from fitwitness.claims.models import ClaimReview

            human_input = ClaimReview.model_validate(human_input).model_dump(mode="json")
        else:
            from fitwitness.contracts import ReviewInput

            human_input = ReviewInput.model_validate(human_input).model_dump(mode="json")
        with self.repo.connection(scope) as c:
            row = c.execute(
                "UPDATE fw_runs SET state='queued',human_input=%s,question=NULL,lease_until=NULL WHERE id=%s AND state='waiting_input' RETURNING id",
                (Jsonb(human_input), run_id),
            ).fetchone()
            if not row:
                raise ValueError("실행이 담당자 검토 대기 상태가 아닙니다")
            c.execute("INSERT INTO fw_dispatch(tenant_id,run_id) VALUES(%s,%s) ON CONFLICT DO NOTHING", (scope.tenant_id, run_id))
            self._event(c, scope, run_id, "resumed_by_human", {"reviewer": human_input.get("reviewer"),
                        "decisions": len(human_input.get("decisions") or {}), "outcome": human_input.get("outcome")})
        return self.get(scope, run_id)

    def release_crashed(self, scope, run_id, token=None):
        with self.repo.connection(scope) as c:
            c.execute(
                "UPDATE fw_runs SET lease_until=now()-interval '1 second' WHERE id=%s AND state='running' AND (%s::text IS NULL OR lease_token=%s)",
                (run_id, token, token),
            )

    def mark_fault_consumed(self, scope, run_id):
        with self.repo.connection(scope) as c:
            return bool(
                c.execute(
                    "UPDATE fw_runs SET fault_consumed=true WHERE id=%s AND NOT fault_consumed RETURNING id",
                    (run_id,),
                ).fetchone()
            )

    def invalidate(self, scope):
        with self.repo.connection(scope) as c:
            self.repo.lock(c, scope.tenant_id)
            snap = self.repo.snapshot_in(c, scope)
            rs = c.execute(
                "UPDATE fw_runs SET state='stale' WHERE state='completed' AND snapshot_id<>%s AND COALESCE(request->>'kind','drawing')<>'claim' RETURNING id",
                (snap.id,),
            ).fetchall()
            for r in rs:
                self._event(
                    c,
                    scope,
                    r["id"],
                    "stale",
                    {"reason": "도면 개정 후 결과를 다시 확인해야 합니다"},
                )
        return [r["id"] for r in rs]

    def owns_lease(self, scope, run_id, token):
        with self.repo.connection(scope) as c:
            return bool(
                c.execute(
                    "SELECT id FROM fw_runs WHERE id=%s AND lease_token=%s AND state='running' AND lease_until>now()",
                    (run_id, token),
                ).fetchone()
            )

    def save_usage(self, scope, run_id, token, usage):
        with self.repo.connection(scope) as c:
            row = c.execute(
                "UPDATE fw_runs SET usage=%s WHERE id=%s AND lease_token=%s AND state='running' AND lease_until>now() RETURNING id",
                (Jsonb(usage.model_dump(mode="json")), run_id, token),
            ).fetchone()
            if not row:
                raise RuntimeError("실행 소유권 만료")

    def pending(self, limit=100):
        with self.repo.plain() as c:
            rows = c.execute(
                "SELECT tenant_id,run_id FROM fw_dispatch ORDER BY created_at LIMIT %s",
                (limit,),
            ).fetchall()
        return [(r["tenant_id"], r["run_id"]) for r in rows]

    def drop_dispatch(self, scope, run_id):
        with self.repo.connection(scope) as c:
            c.execute("DELETE FROM fw_dispatch WHERE run_id=%s", (run_id,))

    def admit(self, bucket, limit):
        with self.repo.plain() as c:
            row = c.execute(
                "INSERT INTO fw_admission(bucket,used) VALUES(%s,1) ON CONFLICT(bucket) DO UPDATE SET used=fw_admission.used+1 WHERE fw_admission.used<%s RETURNING used",
                (bucket, limit),
            ).fetchone()
            return row is not None

    def register_session(self, scope):
        with self.repo.plain() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS fw_sessions (tenant_id text PRIMARY KEY,expires_at timestamptz NOT NULL)"
            )
            c.execute(
                "INSERT INTO fw_sessions VALUES(%s,now()+interval '1 hour')",
                (scope.tenant_id,),
            )

    def cleanup_sessions(self):
        from fitwitness.contracts import TenantScope

        with self.repo.plain() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS fw_sessions (tenant_id text PRIMARY KEY,expires_at timestamptz NOT NULL)"
            )
            expired = c.execute(
                "SELECT tenant_id FROM fw_sessions WHERE expires_at<now() LIMIT 20"
            ).fetchall()
        for row in expired:
            tenant = row["tenant_id"]
            s = TenantScope(tenant_id=tenant, user_id="retention")
            with self.repo.connection(s) as c:
                self.repo.lock(c, tenant)
                for table in (
                    "fw_dispatch",
                    "fw_events",
                    "fw_runs",
                    "fw_vectors",
                    "fw_facts",
                    "fw_assets",
                    "fw_claim_docs",
                    "fw_claim_cases",
                    "fw_payout_keys",
                    "fw_payouts",
                    "fw_saved_tools",
                ):
                    c.execute(f"DELETE FROM {table} WHERE tenant_id=%s", (tenant,))
                # Clear child revisions first to respect the revision DAG foreign key.
                c.execute(
                    "UPDATE fw_revisions SET supersedes=NULL WHERE tenant_id=%s",
                    (tenant,),
                )
                c.execute("DELETE FROM fw_revisions WHERE tenant_id=%s", (tenant,))
            with self.repo.plain() as c:
                for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    if c.execute("SELECT to_regclass(%s) AS t", (table,)).fetchone()["t"]:
                        c.execute(
                            f"DELETE FROM {table} WHERE thread_id LIKE %s",
                            (tenant + ":%",),
                        )
                c.execute("DELETE FROM fw_sessions WHERE tenant_id=%s", (tenant,))
                c.execute(
                    "DELETE FROM fw_admission WHERE created_at<now()-interval '2 days'"
                )
