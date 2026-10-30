"""Same-origin demo API. Each visitor receives a signed, isolated workspace."""

from __future__ import annotations
from typing import Literal
import functools, hashlib, hmac, json, os, re, secrets, subprocess, sys, threading, time
from contextlib import asynccontextmanager
from fitwitness.runtime.dispatcher import Dispatcher
from fitwitness.runtime.pool import WarmWorkers
from pathlib import Path
from uuid import uuid4
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from decimal import Decimal
from fitwitness.api import identity, live
from fitwitness.contracts import Budget, TenantScope, RunRequest, DrawingRevision, SearchRequest, ReviewInput
from fitwitness.claims.models import ClaimRequest, ClaimReview, ClaimSupplement, Policy
from fitwitness.claims.synth import SCENARIO_LABELS
from fitwitness.runtime.metrics import collect as collect_db_metrics
from fitwitness.runtime.trace import build_trace
from fitwitness.storage.repository import Repository
from fitwitness.runtime.jobs import Jobs, QuotaExceeded
from fitwitness.ingest.pdf import extract_pdf
from fitwitness.retrieval.pipeline import search
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "var/corpus"


MAX_BODY_BYTES = 64 * 1024
# The bundle is self-contained: no external script, font, or style is loaded. The viewer draws into canvases and
# blob URLs; React sets inline style attributes. Nothing may frame the app.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'none'")


def create_app():
    dsn = os.getenv(
        "FITWITNESS_DATABASE_URL", "postgresql://fwadmin@/postgres?host=/tmp&port=55439"
    )
    repo = Repository(dsn)
    jobs = Jobs(repo)
    key = os.getenv("FITWITNESS_SESSION_SECRET")
    if key and os.getenv("RENDER") and (len(key) < 32 or "replace" in key.lower()):
        raise RuntimeError("FITWITNESS_SESSION_SECRET must be a random string of at least 32 characters in hosted deployments")
    if not key:
        if os.getenv("RENDER"):
            raise RuntimeError(
                "FITWITNESS_SESSION_SECRET is required in hosted deployments"
            )
        path = ROOT / "var/session.key"
        path.parent.mkdir(exist_ok=True)
        try:  # created owner-only from the start, and by exactly one process if several start at once
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_urlsafe(48))
        except FileExistsError:
            pass
        for _ in range(50):  # another process may still be writing the key it just created
            key = path.read_text().strip()
            if key:
                break
            time.sleep(0.1)
    signer = URLSafeTimedSerializer(key, salt="fitwitness-session-v1")
    from fitwitness.claims import receipt as receipts

    approval_key = receipts.signing_key(key.encode())

    # Reviewer identity: with an OIDC issuer configured every answer must carry an ID token, and the reviewer the run
    # records is the account it proves. Not configured (the public demo): the typed name, as it says on the screen.
    who = identity.load()
    verifier = identity.Verifier(who) if who.enabled else None

    def sign_answer(run, answer, step, prev):
        claim_id = ((run.get("request") or {}).get("claim") or {}).get("claim_id", "")
        return receipts.sign(approval_key, run["tenant_id"], run["id"], claim_id, answer, step=step, prev=prev)

    # The per-address session count is stored under a keyed hash of the address: a bare SHA-256 of an IPv4
    # address is undone by trying every address.
    peer_key = hmac.new(key.encode(), b"fitwitness-peer-v1", hashlib.sha256).digest()

    # Runs execute in their own process; warm ones have already paid the interpreter and
    # import cost (seconds on a shared CPU) before a job arrives. Without the lifespan
    # (tests) nothing is pre-warmed and a worker is spawned cold per run, as before.
    workers = WarmWorkers(
        [sys.executable, "-m", "fitwitness.runtime.worker", "--pool"],
        env={**{k: v for k, v in os.environ.items() if k not in ("FITWITNESS_SESSION_SECRET", "FITWITNESS_METRICS_TOKEN")},
             "PYTHONPATH": str(ROOT / "src"), "FITWITNESS_DATABASE_URL": dsn},
        cwd=ROOT,
        size=int(os.getenv("FITWITNESS_WARM_WORKERS", "1")),
    )

    @asynccontextmanager
    async def lifespan(app):
        dispatcher = Dispatcher(jobs, supervise)
        app.state.dispatcher = True
        app.state.nudge = dispatcher.nudge
        workers.start()
        dispatcher.start()
        threading.Thread(target=lambda: [corpus_facts(e["id"]) for e in demo_entries()], daemon=True).start()
        yield
        dispatcher.close()
        workers.close()

    # The interactive pages (/docs, /redoc) load their scripts from a public CDN, which a server with no outside
    # connection cannot reach and which sends every visitor's browser to a third party. They are off unless the
    # operator turns them on; the schema itself stays at /openapi.json.
    interactive_docs = os.getenv("FITWITNESS_API_DOCS", "off").strip().lower() in ("on", "1", "true")
    app = FastAPI(title="FitWitness", version="0.1.0", lifespan=lifespan,
                  docs_url="/docs" if interactive_docs else None, redoc_url="/redoc" if interactive_docs else None)
    app.state.dispatcher = False
    app.state.nudge = lambda: None
    app.state.workers = workers
    app.state.repo = repo
    app.state.jobs = jobs
    allowed = set(
        os.getenv(
            "FITWITNESS_ALLOWED_ORIGINS",
            "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8787,http://localhost:8787,http://testserver",
        ).split(",")
    )
    registry = CollectorRegistry()
    requests = Counter(
        "fitwitness_requests_total",
        "API requests",
        ["method", "status"],
        registry=registry,
    )
    latency = Histogram(
        "fitwitness_request_seconds", "Request duration", registry=registry
    )

    @app.middleware("http")
    async def guards(request: Request, call_next):
        start = time.monotonic()
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and request.headers.get("origin")
            and request.headers["origin"] not in allowed
        ):
            return Response("Origin not allowed", status_code=403)
        if request.method in ("POST", "PUT", "PATCH"):
            # Every JSON body this API takes is a few hundred bytes. The framework reads a body before it looks at the
            # session cookie, so without a cap an anonymous client could make the process buffer whatever it sends.
            length = request.headers.get("content-length")
            if length is None and "chunked" in request.headers.get("transfer-encoding", "").lower():
                return Response("Content-Length required", status_code=411)
            if length is not None and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
                return Response("Request body too large", status_code=413)
        try:
            response = await call_next(request)
        except Exception:
            # An unhandled error becomes a 500 outside this middleware; count it here, or the 5xx alert never sees it.
            requests.labels(request.method, "500").inc()
            latency.observe(time.monotonic() - start)
            raise
        requests.labels(request.method, str(response.status_code)).inc()
        latency.observe(time.monotonic() - start)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        response.headers["Cache-Control"] = (
            "no-store" if request.url.path.startswith("/api") else "no-cache"
        )
        return response

    def reserve_live_run(s: TenantScope, idem: str) -> dict:
        """The model fields of a live claim run, after the visitor's turn and the day's ceiling have both been taken."""
        cfg = live.load()
        if not cfg.enabled:
            raise HTTPException(409, "라이브 모델 읽기는 이 서버에서 꺼져 있습니다")
        key = f"live-key:{s.tenant_id}:{idem}"
        if jobs.admit(key, 1):  # a repeated key is the same run: it reserves nothing again
            taken = []
            try:
                if not jobs.admit(f"live-session:{s.tenant_id}", cfg.per_session):
                    raise HTTPException(429, f"체험 공간마다 라이브 모델 읽기는 {cfg.per_session}번까지입니다")
                taken.append(f"live-session:{s.tenant_id}")
                if not repo.live_reserve(live.today(), cfg.run_usd, cfg.daily_usd):
                    raise HTTPException(429, "오늘의 라이브 모델 호출 한도를 모두 썼습니다. 규칙 읽기로는 계속 쓸 수 있습니다")
            except HTTPException:
                # Nothing was reserved, so the key must not read as reserved: the same key sent again is checked again.
                for bucket in (key, *taken):
                    jobs.release_admission(bucket)
                raise
        return {"provider": "anthropic", "model_id": cfg.model,
                "budget": Budget(max_model_calls=3, max_tool_calls=1, max_tokens=12000, max_cost_usd=cfg.run_usd, deadline_seconds=90)}

    def run_quota():
        """The hourly budget for new runs, shared by every route that starts one."""
        return ("runs:" + str(int(time.time() // 3600)), 200)

    def scope(request: Request):
        cookie = request.cookies.get("fw_session")
        if not cookie:
            raise HTTPException(401, "체험 공간을 먼저 열어 주세요")
        try:
            data = signer.loads(cookie, max_age=3600)
            return TenantScope.model_validate(data)
        except (BadSignature, SignatureExpired, ValueError):
            raise HTTPException(401, "체험 세션이 만료됐습니다")

    def revision_from(entry, s):
        return DrawingRevision(
            tenant_id=s.tenant_id,
            **{
                k: entry[k]
                for k in [
                    "id",
                    "document_id",
                    "drawing_number",
                    "family_id",
                    "revision_label",
                    "supersedes",
                    "kind",
                    "title",
                    "source_hash",
                ]
            },
        )

    @functools.lru_cache(maxsize=512)
    def corpus_facts(entry_id: str):
        """PDF extraction of a corpus entry does not depend on the tenant: do it once per process."""
        entry = next(e for e in json.loads((CORPUS / "manifest.json").read_text())["document_entries"] if e["id"] == entry_id)
        template = revision_from(entry, TenantScope(tenant_id="template", user_id="template"))
        data = (CORPUS / entry["pdf"]).read_bytes()
        return data, extract_pdf(data, template)

    def load_entries(entries, s):
        """Seed corpus entries into a workspace: one transaction for all of them."""
        items = []
        for entry in entries:
            r = revision_from(entry, s)
            data, template_facts = corpus_facts(entry["id"])
            # Facts carry no tenant: source refs point at the revision id and hash.
            items.append((r, data, list(template_facts), {k: (CORPUS / entry[k]).read_bytes() for k in ["png", "step", "mesh"]}))
        repo.seed_revisions(s, items)
        return [r for r, *_ in items]

    def load_entry(entry, s):
        return load_entries([entry], s)[0]

    DEMO_FAMILIES = ("FW-F000", "FW-F001", "FW-F002", "FW-F003")  # bracket, flange, shaft, housing
    CLAIMS = Path(os.getenv("FITWITNESS_CLAIMS_DIR", str(ROOT / "var/claims")))
    DEMO_CLAIM_SCENARIOS = ("clean", "format_variance", "missing_doc", "date_conflict", "waiting_period",
                            "duplicate", "high_amount", "grade_missing")

    def demo_claim_cases():
        """One synthetic claim per demo scenario; nothing when the corpus was not generated."""
        if not (CLAIMS / "manifest.json").exists():
            return []
        cases = json.loads((CLAIMS / "manifest.json").read_text())["cases"]
        picked = []
        for scenario in DEMO_CLAIM_SCENARIOS:
            first = next((c for c in cases if c["scenario"] == scenario), None)
            if first:
                picked.append(first)
        return picked

    def load_claim_cases(cases, s):
        """Seed claim cases with their documents into a workspace: one transaction for all of them."""
        items = []
        for case in cases:
            folder = CLAIMS / case["case_id"]
            documents = []
            for d in [*case["documents"], *case.get("supplementary_documents", [])]:  # the waiting ones are stored with the case
                png = folder / f"{d['id']}.png"
                documents.append((d, (folder / f"{d['id']}.pdf").read_bytes(), png.read_bytes() if png.exists() else None))
            items.append((case, documents, case.get("prior_paid_keys") or []))
        repo.seed_claim_cases(s, items)

    def demo_entries():
        manifest = json.loads((CORPUS / "manifest.json").read_text())["document_entries"]
        return [e for e in manifest if e["family_id"] in DEMO_FAMILIES and not e["is_revision_update"]]

    @app.post("/api/demo-sessions")
    def session(response: Response, request: Request):
        bucket = "sessions:" + str(int(time.time() // 3600))
        peer = hmac.new(
            peer_key, (request.client.host if request.client else "unknown").encode(), hashlib.sha256
        ).hexdigest()[:16]
        per_peer = int(os.getenv("FITWITNESS_SESSION_LIMIT_PER_PEER", "20"))
        # the address's own budget first: an address that is over it must not also use up the shared one
        if not jobs.admit(bucket + ":" + peer, per_peer) or not jobs.admit(bucket, 100):
            raise HTTPException(
                429, "체험 공간 생성 한도입니다. 잠시 후 다시 시도해 주세요."
            )
        jobs.cleanup_sessions()
        s = TenantScope(tenant_id=str(uuid4()), user_id=str(uuid4()), role="operator")
        jobs.register_session(s)
        # One bracket family carries the guided story (five near-identical candidates);
        # one family of each other kind is loaded too, so retrieval has parts to exclude
        # and a flange query finds flanges. The held-back FW-000-0 revision (manifest
        # entry 5) only arrives through /api/demo/revision.
        entries = demo_entries()
        load_entries(entries, s)
        load_claim_cases(demo_claim_cases(), s)
        response.set_cookie(
            "fw_session",
            signer.dumps(s.model_dump()),
            max_age=3600,
            httponly=True,
            samesite="lax",
            secure=bool(os.getenv("RENDER")),
        )
        return {
            "workspace_id": s.tenant_id,
            "documents": len(entries),
            "expires_in": 3600,
            "mode": "isolated_synthetic_demo",
        }

    @app.get("/api/capabilities")
    def capabilities():
        cfg = live.load()
        return {
            "providers": {"rules": True, "openai": False, "anthropic": False},
            # Live model reading of a claim: off unless the operator enabled it (api/live.py). The remaining count is
            # what the day's ceiling still allows, not a promise: another visitor may take it first.
            "live": ({"enabled": True, "model": cfg.model, "per_session": cfg.per_session,
                      "remaining_today": cfg.remaining_runs(Decimal(str(repo.live_reserved(live.today()))))}
                     if cfg.enabled else {"enabled": False}),
            "models": {
                "openai": os.getenv("FITWITNESS_OPENAI_MODEL", ""),
                "anthropic": os.getenv("FITWITNESS_ANTHROPIC_MODEL", ""),
            },
            # Who a reviewer is: an OIDC account when the operator configured an issuer, else the name typed on the screen.
            "identity": {"mode": "oidc", "issuer": who.issuer} if verifier is not None else {"mode": "anonymous"},
            "corpus": "synthetic",
            "version": "0.1.0",
        }

    @app.get("/api/documents")
    def documents(s: TenantScope = Depends(scope)):
        return repo.documents(s)

    @app.get("/api/documents/{rid}/assets/{kind}")
    def asset(rid: str, kind: str, s: TenantScope = Depends(scope)):
        if kind not in ("pdf", "png", "step", "mesh"):
            raise HTTPException(404, "없음")
        data = repo.asset(s, rid, kind)
        if data is None:
            raise HTTPException(404, "자료를 찾을 수 없습니다")
        types = {
            "pdf": "application/pdf",
            "png": "image/png",
            "step": "application/step",
            "mesh": "application/json",
        }
        return Response(data, media_type=types[kind])

    @app.post("/api/search")
    def find(q: SearchRequest, s: TenantScope = Depends(scope)):
        return [c.model_dump(mode="json") for c in search(s, q, repo.snapshot(s), repo)]

    def supervise(s, run_id, fault=False):
        token = str(uuid4())
        job = {"tenant": s.tenant_id, "run": run_id, "token": token, "fault": bool(fault)}
        try:
            code = workers.run(job, timeout=180)
            if code != 0:
                jobs.release_crashed(s, run_id, token)
                # TestClient without lifespan still demonstrates one real restart.
                if code == 86 and not app.state.dispatcher:
                    workers.run({**job, "fault": False}, timeout=180)
        except subprocess.TimeoutExpired:
            # Never borrow a replacement worker's token.
            jobs.fail(s, run_id, token, "worker 실행 시간 초과")

    @app.post("/api/runs")
    def run(
        body: RunRequest,
        request: Request,
        background: BackgroundTasks,
        s: TenantScope = Depends(scope),
    ):
        if body.kind == "claim" or body.claim is not None:
            # A claim run is built by the server from the stored case (/api/claims/cases/{id}/run) or continues a held one
            # (/api/runs/{id}/supplement). A client-supplied claim would choose its own claim id, policy and dates, and with
            # them what the payout ledger and the duplicate check protect.
            raise HTTPException(422, "청구 심사는 청구 건에서 시작해 주세요")
        if body.provider != "rules":
            # Public anonymous demo never has authority to spend provider funds.
            # Live research runs use the CLI with explicit server-side credentials.
            raise HTTPException(
                409,
                "공개 체험에서는 유료 모델 호출이 비활성화되어 있습니다. 운영자 CLI를 사용해 주세요.",
            )
        idem = request.headers.get("idempotency-key") or str(uuid4())
        if len(idem) > 200:
            raise HTTPException(422, "idempotency key too long")
        body = body.model_copy(
            update={"demo_fault": request.headers.get("x-demo-fault") == "1"}
        )
        try:
            view = jobs.enqueue(s, body, idem, quota=run_quota())
        except QuotaExceeded as exc:
            raise HTTPException(429, str(exc))
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        if view.state == "queued" and not app.state.dispatcher:
            background.add_task(
                supervise, s, view.id, request.headers.get("x-demo-fault") == "1"
            )
        app.state.nudge()
        return view

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, s: TenantScope = Depends(scope)):
        r = jobs.get(s, run_id)
        if not r:
            raise HTTPException(404, "실행을 찾을 수 없습니다")
        return r

    @app.post("/api/runs/{run_id}/resume")
    def resume(
        run_id: str,
        body: ReviewInput | ClaimReview,
        background: BackgroundTasks,
        request: Request,
        s: TenantScope = Depends(scope),
    ):
        """A reviewer answers a run parked in waiting_input; the worker resumes the graph with it."""
        if not jobs.get(s, run_id):
            raise HTTPException(404, "실행을 찾을 수 없습니다")
        from fitwitness.claims.review import ReviewRuleError

        answer, account = body.model_dump(mode="json"), None
        if verifier is not None:  # the reviewer is whom the token proves; the typed name and any account in the body are replaced
            try:
                person = verifier.verify(identity.bearer(request.headers.get("authorization")))
            except identity.IdentityError as exc:
                raise HTTPException(401, str(exc))
            answer["reviewer"], account = person.name, person.account
        try:
            view = jobs.resume(s, run_id, answer, signer=lambda run, answer, step, prev: sign_answer({**run, "tenant_id": s.tenant_id}, answer, step, prev),
                               account=account)
        except ReviewRuleError as exc:
            raise HTTPException(422, str(exc))  # the answer breaks the sign-off rules; the run is still waiting
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        if not app.state.dispatcher:
            background.add_task(supervise, s, view.id, False)
        app.state.nudge()
        return view

    @app.get("/api/runs/{run_id}/approvals")
    def approvals(run_id: str, s: TenantScope = Depends(scope)):
        """The reviewer answers recorded on a finished claim run, each checked against the server's signature."""
        from fitwitness.claims.models import ClaimReview

        run = jobs.raw(s, run_id)
        if not run:
            raise HTTPException(404, "실행을 찾을 수 없습니다")
        result = run.get("result") or {}
        claim_id = ((run.get("request") or {}).get("claim") or {}).get("claim_id", "")
        answers = [ClaimReview.model_validate(a) for a in result.get("approvals") or []]
        return {"run_id": run_id, "claim_id": claim_id,
                "approvals": [{**row, "note": a.note, "total_amount": a.total_amount}
                              for row, a in zip(receipts.verify(approval_key, s.tenant_id, run_id, claim_id, answers), answers)]}

    @app.get("/api/runs/{run_id}/trace")
    def trace(run_id: str, format: Literal["tree", "otlp"] = "tree", s: TenantScope = Depends(scope)):
        view = jobs.get(s, run_id)
        if not view:
            raise HTTPException(404, "없음")
        tree = build_trace(run_id, jobs.events(s, run_id))
        if format == "otlp":
            # The same trace as an OTLP/HTTP JSON body: POST it to any collector's /v1/traces.
            from fitwitness.runtime.otlp import trace_to_otlp

            return trace_to_otlp(tree, kind=view.kind, state=view.state)
        return tree

    @app.get("/api/claims/cases")
    def claim_cases(s: TenantScope = Depends(scope)):
        """Synthetic claim cases loaded into this workspace, with their latest run."""
        latest = {}
        for run in jobs.list_runs(s):
            cid = (run["request"].get("claim") or {}).get("case_id")
            if cid and cid not in latest:
                latest[cid] = {"run_id": run["id"], "state": run["state"]}
        return [{**c, "scenario_label": SCENARIO_LABELS.get(c["scenario"], c["scenario"]), "latest_run": latest.get(c["case_id"])}
                for c in repo.claim_cases(s)]

    @app.post("/api/claims/cases/{case_id}/run")
    def run_claim(case_id: str, request: Request, background: BackgroundTasks, s: TenantScope = Depends(scope)):
        case = repo.claim_case(s, case_id)
        if not case:
            raise HTTPException(404, "청구 건을 찾을 수 없습니다")
        review = "always" if request.headers.get("x-claim-review") == "always" else "rules"
        idem = request.headers.get("Idempotency-Key") or str(uuid4())
        if len(idem) > 200:
            raise HTTPException(422, "idempotency key too long")
        model_fields: dict = {}
        if request.headers.get("x-claim-live") == "1":
            model_fields = reserve_live_run(s, idem)
        claim = ClaimRequest(case_id=case["case_id"], claim_id=case["claim_id"], policy=Policy.model_validate(case["policy"]),
                             requested=case["requested"], document_ids=[d["id"] for d in case["documents"]],
                             submitted_at=case["submitted_at"], review=review)
        body = RunRequest(kind="claim", claim=claim.model_dump(mode="json"), demo_fault=request.headers.get("x-demo-fault") == "1",
                          **model_fields)
        try:
            view = jobs.enqueue(s, body, idem, quota=run_quota())
        except QuotaExceeded as exc:
            raise HTTPException(429, str(exc))
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        if view.state == "queued" and not app.state.dispatcher:
            background.add_task(supervise, s, view.id, False)
        app.state.nudge()
        return view

    @app.post("/api/runs/{run_id}/supplement")
    def supplement_claim(run_id: str, body: ClaimSupplement, background: BackgroundTasks, s: TenantScope = Depends(scope)):
        """The claimant sends the documents a reviewer asked for: the held claim is reviewed again as the same claim."""
        from fitwitness.claims import resubmit

        run = jobs.raw(s, run_id)
        if not run:
            raise HTTPException(404, "실행을 찾을 수 없습니다")
        claim = (run["request"] or {}).get("claim") or {}
        case = repo.claim_case(s, claim.get("case_id", ""))
        if not case:
            raise HTTPException(404, "청구 건을 찾을 수 없습니다")
        try:
            planned = resubmit.plan(case, run, body.document_ids)
            # One continuation per hold: the same key returns the run already started, a different set of documents is refused.
            view = jobs.enqueue(s, RunRequest(kind="claim", claim=planned.model_dump(mode="json")), "resubmit:" + run_id, quota=run_quota())
        except resubmit.ResubmitError as exc:
            raise HTTPException(409 if exc.conflict else 422, str(exc))
        except QuotaExceeded as exc:
            raise HTTPException(429, str(exc))
        except ValueError as exc:
            raise HTTPException(409, "이 보류에는 이미 다른 서류가 접수되었습니다" if "idempotency" in str(exc) else str(exc))
        if view.state == "queued" and not app.state.dispatcher:
            background.add_task(supervise, s, view.id, False)
        app.state.nudge()
        return view

    @app.get("/api/claims/cases/{case_id}/documents/{doc_id}/{kind}")
    def claim_document(case_id: str, doc_id: str, kind: str, s: TenantScope = Depends(scope)):
        found = repo.claim_doc(s, doc_id)
        if not found or found[0]["case_id"] != case_id or kind not in ("pdf", "png"):
            raise HTTPException(404, "서류를 찾을 수 없습니다")
        meta, pdf, png = found
        if kind == "png":
            if png is None:
                raise HTTPException(404, "미리보기가 없습니다")
            return Response(png, media_type="image/png")
        return Response(pdf, media_type="application/pdf")

    @app.get("/api/claims/ledger")
    def claim_ledger(s: TenantScope = Depends(scope)):
        return repo.payouts(s)

    @app.get("/api/tools")
    def saved_tools(s: TenantScope = Depends(scope)):
        """Search tools the agent defined for this workspace."""
        return repo.saved_tools(s)

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str, s: TenantScope = Depends(scope)):
        if not jobs.get(s, run_id):
            raise HTTPException(404, "없음")
        return jobs.cancel(s, run_id)

    @app.get("/api/runs/{run_id}/events")
    def events(run_id: str, after: int = 0, s: TenantScope = Depends(scope)):
        if not jobs.get(s, run_id):
            raise HTTPException(404, "없음")
        if after < 0:
            raise HTTPException(422, "invalid event cursor")
        return jobs.events(s, run_id, after)

    @app.post("/api/demo/revision")
    def revision(s: TenantScope = Depends(scope)):
        entry = json.loads((CORPUS / "manifest.json").read_text())["document_entries"][
            5
        ]
        new = load_entry(entry, s)
        affected = jobs.invalidate(s)
        return {
            "revision": new.model_dump(),
            "affected_runs": affected,
            "change": "구멍 간격 40mm → 42mm",
        }

    @app.get("/api/evaluations")
    def evaluations(experiment: Literal["qwen", "claude"] = "qwen"):
        # Published, versioned measurement. Anonymous visitors cannot start paid runs.
        p = ROOT / "docs/evaluation" / ("latest.json" if experiment == "qwen" else "claude.json")
        return (
            json.loads(p.read_text())
            if p.exists()
            else {
                "status": "not_measured",
                "message": "모델 비교 실험은 아직 측정되지 않았습니다.",
            }
        )

    @app.get("/api/evaluations/catalog")
    def evaluation_catalog():
        return {"experiments": [
            {"id": key, "label": label} for key, label, file in (
                ("qwen", "Qwen3 · 로컬", "latest.json"),
                ("claude", "Claude Haiku 4.5 · API", "claude.json"),
            ) if (ROOT / "docs/evaluation" / file).exists()
        ]}

    @app.get("/api/evaluations/models")
    def model_records():
        p = ROOT / "docs/evaluation/model-records.json"
        return json.loads(p.read_text()) if p.exists() else {"status": "not_measured"}

    @app.get("/api/evaluations/scan-examples")
    def scan_examples():
        # Pre-rendered pages and what the scan reader read on them (scripts/build-scan-examples.py). The public demo has
        # no OCR engine and reads nothing here.
        p = ROOT / "docs/evaluation/scan-examples.json"
        return json.loads(p.read_text()) if p.exists() else {"status": "not_measured"}

    @app.get("/api/evaluations/scan-examples/{name}")
    def scan_example_image(name: str):
        folder = ROOT / "docs/evaluation/scan-examples"
        path = folder / name
        if not re.fullmatch(r"[a-z0-9-]+\.jpg", name) or not path.is_file():
            raise HTTPException(404, "no such example page")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/api/evaluations/agent")
    def agent_evaluation(version: Literal["latest", "v1"] = "latest"):
        p = ROOT / "docs/evaluation" / ("agent-v1.json" if version == "v1" else "agent.json")
        return json.loads(p.read_text()) if p.exists() else {"status": "not_measured"}

    @app.get("/api/evaluations/retrieval")
    def retrieval_evaluation(experiment: Literal['baseline','reranking']='baseline'):
        p = ROOT / 'docs/evaluation' / ('reranking.json' if experiment=='reranking' else 'retrieval.json')
        return json.loads(p.read_text()) if p.exists() else {"status": "not_measured"}

    @app.get('/api/evaluations/vision')
    def vision_evaluation():
        p = ROOT / 'docs/evaluation/vision.json'
        return json.loads(p.read_text()) if p.exists() else {"status": "not_measured"}

    @app.get('/api/evaluations/cad')
    def cad_evaluation():
        p = ROOT / 'docs/evaluation/nist-cad.json'
        return json.loads(p.read_text()) if p.exists() else {"status": "not_measured"}

    # HEAD too: uptime monitors that keep the free instance awake may probe with it.
    @app.api_route("/health", methods=["GET", "HEAD"])
    def health():
        return {
            "status": "ok",
            "version": "0.1.0",
            "sha": os.getenv(
                "RENDER_GIT_COMMIT", os.getenv("FITWITNESS_GIT_SHA", "local")
            ),
        }

    @app.get("/ready")
    def ready():
        try:
            with repo.connection(
                TenantScope(tenant_id="readiness", user_id="system")
            ) as c:
                c.execute("SELECT 1 FROM fw_revisions LIMIT 1")
        except Exception:
            raise HTTPException(503, "database unavailable")
        return {"status": "ready"}

    @app.get("/metrics")
    def metrics(request: Request):
        token = os.getenv("FITWITNESS_METRICS_TOKEN")
        if not token or not hmac.compare_digest(request.headers.get("authorization", "").encode(), ("Bearer " + token).encode()):
            raise HTTPException(401)
        try:
            derived = collect_db_metrics(dsn)
        except Exception as exc:  # the HTTP counters still serve when the database is unavailable
            derived = f"# database-derived metrics unavailable: {type(exc).__name__}\n"
        return Response(generate_latest(registry).decode() + derived, media_type="text/plain")

    if (ROOT / "web/dist").exists():
        app.mount("/", StaticFiles(directory=ROOT / "web/dist", html=True), name="web")
    return app
