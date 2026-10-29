"""Signed receipts for reviewer answers: who decided what on which claim, tamper-evident.

When the API accepts a reviewer's answer it signs a canonical message (tenant, run, claim, step, reviewer, outcome,
amount, note, time, and the first answer's signature when this is the second) with a key only the API process holds.
The worker, which moves the money, never sees the key; it copies the receipts into the run's result. Checking
recomputes the signatures, so an answer edited in the database afterwards, or a second approval attached to a different
first one, no longer verifies.

What this proves is that the server accepted that answer from that session at that time. It does not prove who the
reviewer is: the demo has no accounts, and the reviewer is whatever name the caller typed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import hashlib
import hmac
import json

from fitwitness.claims.models import ApprovalReceipt, ClaimReview

PURPOSE = b"fitwitness/approval-receipt/v1"


def signing_key(secret: bytes) -> bytes:
    """A key for receipts derived from the server secret, so the session cookie's key is not used for anything else."""
    return hmac.new(secret, PURPOSE, hashlib.sha256).digest()


def _message(tenant_id: str, run_id: str, claim_id: str, answer: ClaimReview, step: int, decided_at: str, prev: str) -> bytes:
    return json.dumps({
        "tenant": tenant_id, "run": run_id, "claim": claim_id, "step": step, "reviewer": answer.reviewer,
        "outcome": answer.outcome, "total_amount": answer.total_amount, "note": answer.note, "decided_at": decided_at, "prev": prev,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(key: bytes, tenant_id: str, run_id: str, claim_id: str, answer: ClaimReview, *, step: int, prev: str = "",
         now: datetime | None = None) -> ApprovalReceipt:
    decided_at = (now or datetime.now(timezone.utc)).isoformat()
    signature = hmac.new(key, _message(tenant_id, run_id, claim_id, answer, step, decided_at, prev), hashlib.sha256).hexdigest()
    return ApprovalReceipt(step=step, decided_at=decided_at, prev=prev, signature=signature, key_id=sha256(key).hexdigest()[:8])


def verify(key: bytes, tenant_id: str, run_id: str, claim_id: str, answers: list[ClaimReview]) -> list[dict]:
    """One row per answer: whether its receipt is the signature of exactly these fields, and whether it follows the
    answer before it. An answer without a receipt (a run from before receipts, or a path that did not sign) is
    ``unsigned``, not valid."""
    rows, previous = [], ""
    for answer in answers:
        receipt = answer.receipt
        if receipt is None:
            rows.append({"reviewer": answer.reviewer, "outcome": answer.outcome, "status": "unsigned"})
            previous = ""
            continue
        expected = hmac.new(key, _message(tenant_id, run_id, claim_id, answer, receipt.step, receipt.decided_at, receipt.prev),
                            hashlib.sha256).hexdigest()
        intact = hmac.compare_digest(expected, receipt.signature)
        chained = receipt.prev == previous
        rows.append({"reviewer": answer.reviewer, "outcome": answer.outcome, "step": receipt.step, "decided_at": receipt.decided_at,
                     "key_id": receipt.key_id, "status": "valid" if intact and chained else ("altered" if not intact else "out_of_order")})
        previous = receipt.signature
    return rows
