"""Verify a deployed demo over HTTPS; never needs a database credential."""

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from http.cookiejar import CookieJar
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4


def verify(base, local=False):
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
    examples = call("/api/evaluations/scan-examples")  # pre-rendered scan readings: the page pictures are served files, not OCR output
    assert examples["status"] == "measured" and examples["examples"], examples
    for example in examples["examples"]:
        picture = call("/api/evaluations/scan-examples/" + example["page"]["image"].split("/")[-1])
        assert picture[:3] == b"\xff\xd8\xff" and len(picture) > 5000, example["id"]
    print(f"Scan examples served: {len(examples['examples'])} pages", flush=True)
    session = call("/api/demo-sessions", {})
    count = session["documents"]  # the demo's drawings: five in October's first demo, four families of five since
    assert count >= 5, session
    assert list(jar) and (local or all(cookie.secure for cookie in jar)), "Hosted cookie must use HTTPS"
    print("Session and database ready", flush=True)
    docs = call("/api/documents")
    assert len(docs) == count, (len(docs), count)
    assets = []
    for doc in docs:
        hashes = {}
        for kind, signature in [("pdf", b"%PDF"), ("png", b"\x89PNG"), ("step", b"ISO-10303-21")]:
            data = call(f"/api/documents/{doc['id']}/assets/{kind}")
            assert data.startswith(signature), (doc["drawing_number"], kind)
            hashes[kind] = sha256(data).hexdigest()
        assert hashes["pdf"] == doc["source_hash"]
        mesh = call(f"/api/documents/{doc['id']}/assets/mesh")
        assert mesh["vertices"] and mesh["faces"]
        found = call("/api/search", {"text": doc["drawing_number"]})
        assert found[0]["revision_id"] == doc["id"]
        assets.append(
            {
                "drawing": doc["drawing_number"],
                "revision": doc["revision_label"],
                "hashes": hashes,
                "vertices": len(mesh["vertices"]),
                "faces": len(mesh["faces"]),
            }
        )
    assert len({a["hashes"]["png"] for a in assets}) == count
    body = {"search": {"text": "구멍 간격 40mm SUS304 브래킷"}, "provider": "rules"}
    key = str(uuid4())
    run = call("/api/runs", body, {"Idempotency-Key": key, "X-Demo-Fault": "1"})
    assert call("/api/runs", body, {"Idempotency-Key": key, "X-Demo-Fault": "1"})["id"] == run["id"]
    completed = wait(run["id"])
    before = Counter(x["verdict"] for x in completed["decisions"])
    assert before == {"match": 2, "mismatch": 3, "unknown": 1}, before
    events = call(f"/api/runs/{run['id']}/events")
    kinds = Counter(x["kind"] for x in events)
    assert kinds["completed"] == 1 and kinds["retrieved"] == 1, kinds
    assert kinds["resumed"] >= 1, kinds
    assert completed["usage"]["model_calls"] == 0
    print("Worker recovery and initial verdicts verified", flush=True)
    call("/api/runs", {**body, "provider": "openai"}, expected=409)
    call("/api/runs", body, {"Origin": "https://untrusted.invalid"}, expected=403)
    update = call("/api/demo/revision", {})
    assert run["id"] in update["affected_runs"]
    assert call(f"/api/runs/{run['id']}")["state"] == "stale"
    second = call("/api/runs", body)
    revised = wait(second["id"])
    after = Counter(x["verdict"] for x in revised["decisions"])
    assert after == {"match": 1, "mismatch": 4, "unknown": 1}, after
    print("Revision invalidation and revised verdicts verified", flush=True)
    # A reviewer holds a claim for documents: nothing is paid and nothing is refused (claims desk, 2026-10-28).
    ledger_before = call("/api/claims/ledger")
    missing = next(c for c in call("/api/claims/cases") if c["scenario"] == "missing_doc")
    parked = call(f"/api/claims/cases/{missing['case_id']}/run", {})
    end = time.monotonic() + 150
    while call(f"/api/runs/{parked['id']}")["state"] != "waiting_input":
        assert time.monotonic() < end, "the claim did not reach a reviewer"
        time.sleep(2)
    call(f"/api/runs/{parked['id']}/resume", {"outcome": "REQUEST_INFO", "reviewer": "hosted check", "note": " "}, expected=422)
    call(f"/api/runs/{parked['id']}/resume", {"outcome": "REQUEST_INFO", "reviewer": "hosted check", "note": "수술확인서 사본 요청"})
    claim = wait(parked["id"])["claim"]
    assert claim["decision"]["outcome"] == "REVIEW" and claim["decision"]["total_amount"] == 0, claim["decision"]
    assert claim["human"]["outcome"] == "REQUEST_INFO" and claim["payout"]["status"] == "skipped", claim["human"]
    assert any(r["rule_id"] == "R-HUMAN-02" for r in claim["decision"]["reasons"])
    assert call("/api/claims/ledger") == ledger_before, "a held claim must not reach the ledger"
    notice = next(n for n in claim["notices"] if n["kind"] == "request_info")  # the claimant is told what to send, and by when
    assert notice["respond_by"] and "수술확인서 사본 요청" in notice["body"], notice
    answers = call(f"/api/runs/{parked['id']}/approvals")["approvals"]  # and the reviewer's answer carries the server's signature
    assert [a["status"] for a in answers] == ["valid"], answers
    assert call("/api/capabilities")["live"]["enabled"] in (True, False)
    print("Claim held for documents: nothing paid, nothing refused", flush=True)
    # The claimant's documents continue the same claim: one new run reads the full set, a second click is the same run,
    # and nothing reaches the ledger before the sign-off this claim's amount needs.
    waiting = [d["id"] for d in missing["supplementary_documents"]]
    again = call(f"/api/runs/{parked['id']}/supplement", {"document_ids": waiting})
    assert again["id"] != parked["id"]
    assert call(f"/api/runs/{parked['id']}/supplement", {"document_ids": waiting})["id"] == again["id"], (
        "a second click must be the same run"
    )
    call(f"/api/runs/{parked['id']}/supplement", {"document_ids": ["nope"]}, expected=422)
    end = time.monotonic() + 150
    while call(f"/api/runs/{again['id']}")["state"] not in ("waiting_input", "completed"):
        assert time.monotonic() < end, "the continued claim did not finish its review"
        time.sleep(2)
    events_again = call(f"/api/runs/{again['id']}/events")
    intake = next(e for e in events_again if e["kind"] == "intake")["payload"]
    assert intake["resubmission"]["of_run"] == parked["id"] and set(waiting) <= {d["id"] for d in intake["documents"]}, intake
    if call(f"/api/runs/{again['id']}")["state"] == "waiting_input":  # still waiting for a sign-off: nothing may have been paid
        assert call("/api/claims/ledger") == ledger_before, "a claim waiting for a sign-off must not reach the ledger"
    print("Documents sent after the hold continue the same claim", flush=True)
    call("/api/demo-sessions", {})
    call(f"/api/runs/{run['id']}", expected=404)
    assert len(call("/api/documents")) == count
    return {
        "url": base,
        "sha": health["sha"],
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed",
        "before": dict(before),
        "after": dict(after),
        "events": dict(kinds),
        "assets": assets,
        "checks": [
            "readiness",
            "secure cookie",
            f"all {count} PDF/PNG/STEP/mesh assets and exact-ID search",
            "idempotency",
            "worker recovery",
            "revision invalidation",
            "reverification",
            "tenant isolation",
            "origin rejection",
            "paid API disabled",
            "private metrics",
            "scan examples and their page pictures",
            "claim held for documents without payout",
            "held claim carries a claimant notice with a deadline and a signed reviewer answer",
            "documents sent after a hold continue the same claim",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", default="artifacts/hosted-verification.json")
    parser.add_argument("--local", action="store_true", help="a local http server: the session cookie is not marked secure")
    args = parser.parse_args()
    result = verify(args.url.rstrip("/"), args.local)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))
