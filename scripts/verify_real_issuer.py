"""The reviewer-identity check against a REAL OpenID issuer: GitHub Actions' own ID tokens.

    python scripts/verify_real_issuer.py --output artifacts/oidc-real-issuer.json

``api/identity.py`` was tested only against an issuer the tests make up (tests/identity_helpers.py): keys the test
generates, a JWKS file it writes. This script asks a real issuer for tokens instead. A GitHub Actions job with
``permissions: id-token: write`` can request an ID token for any audience from ``ACTIONS_ID_TOKEN_REQUEST_URL``; GitHub
signs it with a key it publishes at ``https://token.actions.githubusercontent.com/.well-known/jwks`` (RS256) and lists
that address in its ``openid-configuration``. So the verifier is exercised against keys it has not seen before, found by
discovery, and against a token whose claims it did not write.

It checks, in order:

1. the keys are found by discovery and by the explicit JWKS address, and a token for this service verifies in both;
2. the account is ``<issuer>#<sub>``, the subject the real token carries;
3. every way of refusing: a token for another audience, a payload changed after signing, the algorithm swapped, the
   signature cut or removed, an issuer spelled differently, a token past its expiry (and not one inside the 60 seconds
   of leeway);
4. the API end to end: a claim parked for a person is refused without a token and with each bad token and is answered
   with the real one, after which the run records the token's account and the signed receipt names it.

This is the issuer GitHub runs, not a company's single sign-on: no roles, no groups, no login page. What it shows is that
the signature, issuer, audience and expiry checks hold against a real issuer's keys and tokens. Tokens are never
printed or written; the report holds only which checks passed and the non-secret claims of the token that was used.
"""

import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import quote

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = ISSUER + "/.well-known/jwks"
AUDIENCE = "fitwitness-oidc-check"


def _b64(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _enc(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _parts(token: str) -> tuple[dict, dict, str, str, str]:
    head, body, sig = token.split(".")
    return json.loads(_b64(head)), json.loads(_b64(body)), head, body, sig


def github_token_source():
    """A function ``audience -> ID token`` that asks the GitHub Actions runner for one."""
    import httpx

    url, bearer = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"], os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]

    def get(audience: str) -> str:
        response = httpx.get(f"{url}&audience={quote(audience)}", headers={"Authorization": f"Bearer {bearer}"}, timeout=20)
        response.raise_for_status()
        return response.json()["value"]

    return get


def mutations(token: str) -> dict[str, str]:
    """Tokens made from a good one by the ways an attacker can try, each of which a verifier must refuse."""
    header, claims, head, body, sig = _parts(token)
    other_claims = dict(claims, sub="repo:attacker/attacker:ref:refs/heads/main")
    return {
        "payload changed after signing": f"{head}.{_enc(json.dumps(other_claims).encode())}.{sig}",
        "algorithm swapped to HS256": f"{_enc(json.dumps(dict(header, alg='HS256')).encode())}.{body}.{sig}",
        "algorithm none, no signature": f"{_enc(json.dumps(dict(header, alg='none')).encode())}.{body}.",
        "signature cut short": f"{head}.{body}.{sig[:-12]}",
        "key id the issuer does not publish": f"{_enc(json.dumps(dict(header, kid='not-a-published-key')).encode())}.{body}.{sig}",
        "not a token": "abc.def",
    }


def check_verifier(identity, get_token, *, issuer: str, jwks_file: str = "") -> tuple[list[dict], dict]:
    """Steps 1 to 3. ``jwks_file`` is for a dry run against a stand-in issuer; the real run leaves it empty."""
    results: list[dict] = []

    def record(name: str, ok: bool, detail: str = ""):
        results.append({"check": name, "ok": bool(ok), "detail": detail})
        print(("ok   " if ok else "FAIL ") + name + (f": {detail}" if detail else ""), flush=True)

    def refused(verifier, token: str, **kw) -> str | None:
        try:
            verifier.verify(token, **kw)
        except identity.IdentityError as exc:
            return str(exc)
        return None

    token = get_token(AUDIENCE)
    header, claims, *_ = _parts(token)
    safe = {
        k: claims.get(k) for k in ("iss", "aud", "sub", "repository", "repository_owner", "ref", "event_name", "workflow", "iat", "exp")
    }
    safe["alg"], safe["kid_published"] = header.get("alg"), bool(header.get("kid"))

    by_discovery = identity.Verifier(identity.IdentityConfig(issuer=issuer, audience=AUDIENCE, jwks_file=jwks_file))
    explicit = identity.Verifier(
        identity.IdentityConfig(issuer=issuer, audience=AUDIENCE, jwks_file=jwks_file, jwks_url="" if jwks_file else JWKS_URL)
    )
    for label, verifier in (("keys found by discovery", by_discovery), ("keys from the explicit JWKS address", explicit)):
        try:
            person = verifier.verify(token)
            record(f"a token for this service verifies, {label}", True, f"account {person.account}")
        except Exception as exc:  # noqa: BLE001 - the failure is the finding
            record(f"a token for this service verifies, {label}", False, f"{type(exc).__name__}: {exc}")
            return results, safe
    person = by_discovery.verify(token)
    record("the account is <issuer>#<sub> of the real token", person.account == f"{issuer}#{claims['sub']}", person.account)
    record("the display name falls back to the subject (GitHub tokens carry no name or email)", person.name == str(claims["sub"])[:100])

    other = get_token("someone-else")
    why = refused(by_discovery, other)
    record("a real token for another audience is refused", why is not None, why or "accepted")
    for name, bad in mutations(token).items():
        why = refused(by_discovery, bad)
        record(f"refused: {name}", why is not None, why or "accepted")
    slashed = identity.Verifier(identity.IdentityConfig(issuer=issuer + "/", audience=AUDIENCE, jwks_file=jwks_file))
    why = refused(slashed, token)
    record("refused: the issuer spelled with a trailing slash", why is not None, why or "accepted")
    why = refused(by_discovery, token, now=claims["exp"] + 3600)
    record("refused: the same token an hour after it expired", why is not None, why or "accepted")
    record("accepted: 30 seconds after expiry, inside the leeway", refused(by_discovery, token, now=claims["exp"] + 30) is None)
    record("accepted: a fresh token again, keys already cached", refused(by_discovery, get_token(AUDIENCE)) is None)
    return results, safe


def check_api(identity, get_token, *, issuer: str, jwks_file: str = "") -> list[dict]:
    """Step 4: the reviewer an answer records is whom the real token proves."""
    from fastapi.testclient import TestClient

    from fitwitness.api.app import create_app
    from fitwitness.claims.synth import generate_cases

    results: list[dict] = []

    def record(name: str, ok: bool, detail: str = ""):
        results.append({"check": name, "ok": bool(ok), "detail": detail})
        print(("ok   " if ok else "FAIL ") + name + (f": {detail}" if detail else ""), flush=True)

    with tempfile.TemporaryDirectory() as tmp:
        generate_cases(Path(tmp) / "claims", n=11, seed=5)
        os.environ.update(FITWITNESS_CLAIMS_DIR=str(Path(tmp) / "claims"), FITWITNESS_OIDC_ISSUER=issuer, FITWITNESS_OIDC_AUDIENCE=AUDIENCE)
        os.environ.pop("FITWITNESS_OIDC_JWKS_URL", None)
        if jwks_file:
            os.environ["FITWITNESS_OIDC_JWKS_FILE"] = jwks_file
        else:
            os.environ.pop("FITWITNESS_OIDC_JWKS_FILE", None)  # the keys are found through the issuer's own discovery document
        client = TestClient(create_app())
        client.post("/api/demo-sessions")
        record(
            "the API reports the reviewer as an OIDC account",
            client.get("/api/capabilities").json()["identity"] == {"mode": "oidc", "issuer": issuer},
        )
        run_id = amount = None
        for i, case in enumerate(client.get("/api/claims/cases").json()):
            run = client.post(f"/api/claims/cases/{case['case_id']}/run", headers={"Idempotency-Key": f"real-issuer-{i}"}).json()
            if client.get(f"/api/runs/{run['id']}").json()["state"] != "waiting_input":
                continue
            asked = [e for e in client.get(f"/api/runs/{run['id']}/events").json() if e["kind"] == "waiting_input"][-1]["payload"]
            if asked["total_amount"] > 0:
                run_id, amount = run["id"], asked["total_amount"]
                break
        record("a claim is parked for a person with something to pay", run_id is not None)
        if run_id is None:
            return results
        body = {"outcome": "APPROVE", "reviewer": "typed name", "note": "", "account": "forged#account"}
        good = get_token(AUDIENCE)
        bad = {
            "no token": None,
            "a real token for another audience": get_token("someone-else"),
            "a real token with its payload changed": mutations(good)["payload changed after signing"],
        }
        for name, token in bad.items():
            status = client.post(
                f"/api/runs/{run_id}/resume", json=body, headers={"Authorization": f"Bearer {token}"} if token else {}
            ).status_code
            record(f"the answer is refused with 401: {name}", status == 401, f"status {status}")
        record("a refused answer leaves the run waiting", client.get(f"/api/runs/{run_id}").json()["state"] == "waiting_input")
        ok = client.post(f"/api/runs/{run_id}/resume", json=body, headers={"Authorization": f"Bearer {good}"})
        record("the answer with the real token is accepted", ok.status_code == 200, f"status {ok.status_code}")
        view = client.get(f"/api/runs/{run_id}").json()
        sub = _parts(good)[1]["sub"]
        human = (view.get("claim") or {}).get("human") or {}
        record("the run completes and pays once", view["state"] == "completed" and view["claim"]["payout"]["amount"] == amount)
        record(
            "the recorded account is the real token's, not the forged one",
            human.get("account") == f"{issuer}#{sub}",
            str(human.get("account")),
        )
        record("the typed reviewer name was replaced", human.get("reviewer") != "typed name", str(human.get("reviewer")))
        rows = client.get(f"/api/runs/{run_id}/approvals").json()["approvals"]
        record(
            "the signed receipt is valid and names the account",
            [(r["status"], r["account"]) for r in rows] == [("valid", f"{issuer}#{sub}")],
        )
    return results


def run(get_token, *, issuer: str = ISSUER, jwks_file: str = "") -> dict:
    from fitwitness.api import identity

    started = time.time()
    verifier_results, claims = check_verifier(identity, get_token, issuer=issuer, jwks_file=jwks_file)
    api_results = check_api(identity, get_token, issuer=issuer, jwks_file=jwks_file) if all(r["ok"] for r in verifier_results) else []
    results = verifier_results + api_results
    return {
        "status": "passed" if results and all(r["ok"] for r in results) and api_results else "failed",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "issuer": issuer,
        "audience": AUDIENCE,
        "code_sha": os.getenv("GITHUB_SHA"),
        "token_claims_used": claims,
        "checks": results,
        "seconds": round(time.time() - started, 1),
        "not_shown": "a company's single sign-on: no roles or groups are read from the token and no login page exists; GitHub's tokens carry no name or email",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", default="artifacts/oidc-real-issuer.json")
    args = parser.parse_args()
    if not os.getenv("ACTIONS_ID_TOKEN_REQUEST_URL"):
        raise SystemExit("not a GitHub Actions job with `permissions: id-token: write`: there is no issuer to ask")
    report = run(github_token_source())
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({k: report[k] for k in ("status", "seconds")}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
