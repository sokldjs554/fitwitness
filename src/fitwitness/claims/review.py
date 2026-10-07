"""Who may sign off a claim that stopped for a person, and how many people it takes.

Two rules, both stated as data on the product (a virtual authority matrix, not a real insurer's):

* a claim is **standard** when its payable amount is at or below ``second_approval_above`` (or the product
  has no such threshold) and **senior** above it;
* a **senior** claim, or any claim whose reviewer did not answer in time (the run is *escalated*), is paid
  only when the approver gives a written reason and a second, different person approves too.
  A denial from either person is final: one "no" is enough, two "yes" are needed.

The checks run when an answer arrives (``Jobs.resume``), so a malformed answer is refused at the door and
never stored, and again in the graph, which is the part that actually moves money.

Reviewer names are whatever the caller sends. The public demo has no accounts, so the second approver is
a different *name*; in a deployment this field would be bound to an authenticated identity.
"""

from __future__ import annotations

from fitwitness.claims.models import ClaimReview, Product

NOTE_MIN_CHARS = 10


class ReviewRuleError(ValueError):
    """An answer that breaks the sign-off rules. The API reports it as 422, not as a state conflict."""


def requirements(product: Product, total_amount: int, *, escalated: bool = False) -> dict:
    senior = escalated or (product.second_approval_above is not None and total_amount > product.second_approval_above)
    return {
        "tier": "senior" if senior else "standard",
        "approvals_required": 2 if senior else 1,
        "note_required": senior,
        "escalated": escalated,
        "threshold": product.second_approval_above,
    }


def same_person(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def check_answer(waiting: dict, answer: ClaimReview, *, escalated: bool = False) -> None:
    """Refuse an answer that breaks the rules for the question it answers. ``waiting`` is the question's payload."""
    senior = waiting.get("tier") == "senior" or escalated
    step = int(waiting.get("step") or 1)
    if answer.outcome == "APPROVE" and senior and len(answer.note.strip()) < NOTE_MIN_CHARS:
        raise ReviewRuleError(f"상급 검토 건의 승인에는 사유({NOTE_MIN_CHARS}자 이상)가 필요합니다")
    if step >= 2 and same_person(answer.reviewer, str(waiting.get("first_reviewer") or "")):
        raise ReviewRuleError("2차 승인은 1차 승인자와 다른 담당자여야 합니다")
