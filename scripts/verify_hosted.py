"""Verify a deployed demo over HTTPS; never needs a database credential."""
import argparse
from collections import Counter
from http.cookiejar import CookieJar
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4


def verify(base):
    jar = CookieJar()
    client = build_opener(HTTPCookieProcessor(jar))

    def call(path, data=None, headers=None, expected=200):
        request = Request(base + path, headers={"Origin": base, **(headers or {})})
        if data is not None:
            request.data = json.dumps(data).encode()
            request.add_header("Content-Type", "application/json")
        try:
            response = client.open(request, timeout=90)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            assert response.code == expected, (path, response.code, body[:300])
            if "application/json" in response.headers.get("Content-Type", ""):
                return json.loads(body)
            return body

    def wait(run_id):
        end = time.monotonic() + 150
        while time.monotonic() < end:
            view = call(f"/api/runs/{run_id}")
            if view["state"] == "completed":
                return view
            assert view["state"] not in ("failed", "cancelled"), view
            time.sleep(2)
        raise TimeoutError("Deployed worker did not complete")

    health = call("/health")
    assert call("/ready")["status"] == "ready"
    call("/api/documents", expected=401)
    call("/metrics", expected=401)
    session = call("/api/demo-sessions", {})
    assert session["documents"] == 5
    assert all(cookie.secure for cookie in jar), "Hosted cookie must use HTTPS"
    docs = call("/api/documents")
    for kind, signature in [("pdf", b"%PDF"), ("step", b"ISO-10303-21")]:
        assert call(f"/api/documents/{docs[0]['id']}/assets/{kind}").startswith(signature)
    assert call(f"/api/documents/{docs[0]['id']}/assets/mesh")
    body = {"search": {"text": "구멍 간격 40mm SUS304 브래킷"}, "provider": "rules"}
    key = str(uuid4())
    run = call("/api/runs", body, {"Idempotency-Key": key, "X-Demo-Fault": "1"})
    assert call("/api/runs", body, {"Idempotency-Key": key, "X-Demo-Fault": "1"})["id"] == run["id"]
    completed = wait(run["id"])
    before = Counter(x["verdict"] for x in completed["decisions"])
    assert before == {"match": 2, "mismatch": 2, "unknown": 1}, before
    events = call(f"/api/runs/{run['id']}/events")
    kinds = Counter(x["kind"] for x in events)
    assert kinds["completed"] == 1 and kinds["retrieved"] == 1, kinds
    assert kinds["resumed"] >= 1, kinds
    assert completed["usage"]["model_calls"] == 0
    call("/api/runs", {**body, "provider": "openai"}, expected=409)
    call("/api/runs", body, {"Origin": "https://untrusted.invalid"}, expected=403)
    update = call("/api/demo/revision", {})
    assert run["id"] in update["affected_runs"]
    assert call(f"/api/runs/{run['id']}")["state"] == "stale"
    second = call("/api/runs", body)
    revised = wait(second["id"])
    after = Counter(x["verdict"] for x in revised["decisions"])
    assert after == {"match": 1, "mismatch": 3, "unknown": 1}, after
    call("/api/demo-sessions", {})
    call(f"/api/runs/{run['id']}", expected=404)
    assert len(call("/api/documents")) == 5
    return {"url": base, "sha": health["sha"], "status": "passed", "before": dict(before),
            "after": dict(after), "events": dict(kinds), "checks": ["readiness", "secure cookie",
            "PDF/STEP/mesh", "idempotency", "worker recovery", "revision invalidation",
            "reverification", "tenant isolation", "origin rejection", "paid API disabled", "private metrics"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", default="artifacts/hosted-verification.json")
    args = parser.parse_args()
    result = verify(args.url.rstrip("/"))
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))
