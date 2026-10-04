"""Same-origin demo API. Each visitor receives a signed, isolated workspace."""

from __future__ import annotations
import hashlib, json, os, secrets, subprocess, sys, time
from contextlib import asynccontextmanager
from fitwitness.runtime.dispatcher import Dispatcher
from pathlib import Path
from uuid import uuid4
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fitwitness.contracts import TenantScope, RunRequest, DrawingRevision, SearchRequest
from fitwitness.storage.repository import Repository
from fitwitness.runtime.jobs import Jobs
from fitwitness.ingest.pdf import extract_pdf
from fitwitness.retrieval.pipeline import search
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "var/corpus"


def create_app():
    dsn = os.getenv(
        "FITWITNESS_DATABASE_URL", "postgresql://fwadmin@/postgres?host=/tmp&port=55439"
    )
    repo = Repository(dsn)
    jobs = Jobs(repo)
    key = os.getenv("FITWITNESS_SESSION_SECRET")
    if not key:
        if os.getenv("RENDER"):
            raise RuntimeError(
                "FITWITNESS_SESSION_SECRET is required in hosted deployments"
            )
        path = ROOT / "var/session.key"
        path.parent.mkdir(exist_ok=True)
        if not path.exists():
            path.write_text(secrets.token_urlsafe(48))
            path.chmod(0o600)
        key = path.read_text().strip()
    signer = URLSafeTimedSerializer(key, salt="fitwitness-session-v1")

    @asynccontextmanager
    async def lifespan(app):
        dispatcher = Dispatcher(jobs, supervise)
        app.state.dispatcher = True
        dispatcher.start()
        yield
        dispatcher.close()

    app = FastAPI(title="FitWitness", version="0.1.0", lifespan=lifespan)
    app.state.dispatcher = False
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
        response = await call_next(request)
        requests.labels(request.method, str(response.status_code)).inc()
        latency.observe(time.monotonic() - start)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = (
            "no-store" if request.url.path.startswith("/api") else "no-cache"
        )
        return response

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

    def load_entry(entry, s):
        r = revision_from(entry, s)
        if repo.get_revision(s, r.id):
            return r
        data = (CORPUS / entry["pdf"]).read_bytes()
        facts = extract_pdf(data, r)
        repo.add_revision(s, r, data)
        repo.save_facts(s, r.id, facts)
        for k in ["png", "step", "mesh"]:
            repo.put_asset(s, r.id, k, (CORPUS / entry[k]).read_bytes())
        return r

    @app.post("/api/demo-sessions")
    def session(response: Response, request: Request):
        bucket = "sessions:" + str(int(time.time() // 3600))
        peer = hashlib.sha256(
            (request.client.host if request.client else "unknown").encode()
        ).hexdigest()[:16]
        if not jobs.admit(bucket, 100) or not jobs.admit(bucket + ":" + peer, 20):
            raise HTTPException(
                429, "체험 공간 생성 한도입니다. 잠시 후 다시 시도해 주세요."
            )
        jobs.cleanup_sessions()
        s = TenantScope(tenant_id=str(uuid4()), user_id=str(uuid4()), role="operator")
        jobs.register_session(s)
        # Five near-identical candidates, with a held-back genuine revision update.
        entries = json.loads((CORPUS / "manifest.json").read_text())[
            "document_entries"
        ][:5]
        for entry in entries:
            load_entry(entry, s)
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
        return {
            "providers": {"rules": True, "openai": False, "anthropic": False},
            "models": {
                "openai": os.getenv("FITWITNESS_OPENAI_MODEL", ""),
                "anthropic": os.getenv("FITWITNESS_ANTHROPIC_MODEL", ""),
            },
            "corpus": "synthetic",
            "version": "0.1.0",
        }

    @app.get("/api/documents")
    def documents(s: TenantScope = Depends(scope)):
        active = set(repo.snapshot(s).revision_ids)
        return [
            {
                **r.model_dump(),
                "active": r.id in active,
                "facts": [f.model_dump(mode="json") for f in repo.load_facts(s, r.id)],
            }
            for r in repo.list_revisions(s)
        ]

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
        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "FITWITNESS_DATABASE_URL": dsn,
        }
        token = str(uuid4())
        args = [
            sys.executable,
            "-m",
            "fitwitness.runtime.worker",
            "--tenant",
            s.tenant_id,
            "--run",
            run_id,
            "--token",
            token,
        ]
        try:
            first = subprocess.run(
                args + (["--fault"] if fault else []),
                env=env,
                capture_output=True,
                timeout=180,
                cwd=ROOT,
            )
            if first.returncode != 0:
                jobs.release_crashed(s, run_id, token)
                # TestClient without lifespan still demonstrates one real restart.
                if first.returncode == 86 and not app.state.dispatcher:
                    subprocess.run(
                        args, env=env, capture_output=True, timeout=180, cwd=ROOT
                    )
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
        if not jobs.admit("runs:" + str(int(time.time() // 3600)), 200):
            raise HTTPException(
                429, "전체 실행 한도입니다. 잠시 후 다시 시도해 주세요."
            )
        body = body.model_copy(
            update={"demo_fault": request.headers.get("x-demo-fault") == "1"}
        )
        try:
            view = jobs.enqueue(s, body, idem)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        if view.state == "queued" and not app.state.dispatcher:
            background.add_task(
                supervise, s, view.id, request.headers.get("x-demo-fault") == "1"
            )
        return view

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, s: TenantScope = Depends(scope)):
        r = jobs.get(s, run_id)
        if not r:
            raise HTTPException(404, "실행을 찾을 수 없습니다")
        return r

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

    @app.get("/api/evaluations/agent")
    def agent_evaluation():
        p = ROOT / "docs/evaluation/agent.json"
        return json.loads(p.read_text()) if p.exists() else {"status": "not_measured"}

    @app.get("/health")
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
        if not token or request.headers.get("authorization") != "Bearer " + token:
            raise HTTPException(401)
        return Response(generate_latest(registry), media_type="text/plain")

    if (ROOT / "web/dist").exists():
        app.mount("/", StaticFiles(directory=ROOT / "web/dist", html=True), name="web")
    return app
