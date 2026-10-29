"""What the claimant would be told, and the last day the held claim can still be completed.

A reviewer who asks for documents ends the run with nothing paid and nothing refused; the claimant has to be told what
to send and by when. A finished claim gets its decision in writing too. Both messages are composed here from the
decision, and stored on the run, so the text is what the system decided to say rather than something typed into a
form. Nothing is delivered: the demo has no mail or phone channel, and the screen says so.

The deadline is a number of days from the day the claim was held. It is a synthetic rule for the synthetic product
(``SUPPLEMENT_DAYS``), not a statutory period.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fitwitness.claims.models import DOC_LABELS, ClaimDecision, ClaimReview, Notice

SUPPLEMENT_DAYS = 14
HELD_CODE = "info_requested"


def _today(now: datetime | None) -> date:
    return (now or datetime.now(timezone.utc)).date()


def missing_kinds(decision: ClaimDecision) -> list[str]:
    """Document kinds the rules said a coverage lacked (the message of R-REQ-01 lists them after a colon)."""
    kinds: list[str] = []
    for reason in decision.reasons:
        if reason.code == "missing_documents" and ":" in reason.message:
            for kind in reason.message.rsplit(":", 1)[1].split(","):
                label = DOC_LABELS.get(kind.strip())
                if label and label not in kinds:
                    kinds.append(label)
    return kinds


def request_info(claim_id: str, human: ClaimReview, before: ClaimDecision, *, now: datetime | None = None) -> Notice:
    day = _today(now)
    respond_by = day + timedelta(days=SUPPLEMENT_DAYS)
    lacking = missing_kinds(before)
    body = [f"청구 {claim_id}의 심사를 서류 보완을 위해 보류했습니다. 지급도 부지급도 아직 결정되지 않았습니다.",
            f"담당자({human.reviewer}) 안내: {human.note.strip()}"]
    if lacking:
        body.append("심사 규칙이 확인한 부족 서류: " + ", ".join(lacking))
    body.append(f"{respond_by.isoformat()}까지({SUPPLEMENT_DAYS}일 안에) 서류를 보내 주시면 같은 청구로 다시 심사합니다. 기한이 지나면 새로 청구해야 합니다.")
    return Notice(kind="request_info", subject=f"[보험금 청구 {claim_id}] 서류 보완 요청", body="\n".join(body),
                  respond_by=respond_by.isoformat(), created_at=(now or datetime.now(timezone.utc)).isoformat())


def decision(claim_id: str, outcome: str, explanation: str, *, now: datetime | None = None) -> Notice:
    subject = {"APPROVE": "보험금 지급 안내", "DENY": "보험금 부지급 안내"}.get(outcome, "심사 결과 안내")
    return Notice(kind="decision", subject=f"[보험금 청구 {claim_id}] {subject}", body=explanation,
                  created_at=(now or datetime.now(timezone.utc)).isoformat())


def held_deadline(result: dict | None) -> date | None:
    """The last day documents can be added to a held claim, from the notice composed when it was held. A hold from
    before notices existed has none and does not expire."""
    for notice in (result or {}).get("notices") or []:
        if notice.get("kind") == "request_info" and notice.get("respond_by"):
            try:
                return date.fromisoformat(notice["respond_by"])
            except ValueError:
                return None
    return None
