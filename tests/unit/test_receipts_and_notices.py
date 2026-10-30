"""Signed reviewer receipts, the claimant's notices, and the deadline for completing a held claim."""

from datetime import date, datetime, timezone

import pytest

from fitwitness.claims import notices, receipt
from fitwitness.claims.models import ClaimDecision, ClaimReview, Reason
from fitwitness.claims.resubmit import ResubmitError, plan

KEY = receipt.signing_key(b"x" * 48)
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


def answer(reviewer="김심사", outcome="APPROVE", total_amount=None, note=""):
    return ClaimReview(outcome=outcome, reviewer=reviewer, total_amount=total_amount, note=note)


def signed(a, step=1, prev="", run="run-1", tenant="t-1", claim="CLM-1"):
    return a.model_copy(update={"receipt": receipt.sign(KEY, tenant, run, claim, a, step=step, prev=prev, now=NOW)})


def check(answers, run="run-1", tenant="t-1", claim="CLM-1", key=KEY):
    return [row["status"] for row in receipt.verify(key, tenant, run, claim, answers)]


def test_a_signed_answer_verifies_and_any_edit_breaks_it():
    first = signed(answer(note="서류 확인 완료"))
    assert check([first]) == ["valid"]
    for edit in ({"total_amount": 9_000_000}, {"reviewer": "다른 사람"}, {"outcome": "DENY"}, {"note": "바꾼 사유"}):
        assert check([first.model_copy(update=edit)]) == ["altered"], edit


def test_a_receipt_belongs_to_one_tenant_run_and_claim():
    first = signed(answer())
    assert (
        check([first], run="run-2") == ["altered"]
        and check([first], tenant="t-2") == ["altered"]
        and check([first], claim="CLM-2") == ["altered"]
    )
    assert check([first], key=receipt.signing_key(b"y" * 48)) == ["altered"]


def test_the_second_approval_is_bound_to_the_first_one():
    first = signed(answer("김심사", note="사유 열 글자 이상 적었습니다"))
    second = signed(answer("이승인", note="두 번째 승인 사유입니다"), step=2, prev=first.receipt.signature)
    assert check([first, second]) == ["valid", "valid"]
    other = signed(answer("김심사", total_amount=1, note="다른 첫 답변입니다 열글자"))
    assert check([other, second]) == ["valid", "out_of_order"]  # the second was signed after a different first answer
    assert check([second]) == ["out_of_order"]


def test_an_answer_without_a_receipt_is_unsigned_not_valid():
    assert check([answer()]) == ["unsigned"]


def held(note="진단서를 보내 주세요", reason="진단서"):
    before = ClaimDecision(
        outcome="REVIEW",
        reasons=[
            Reason(rule_id="R-REQ-01", code="missing_documents", severity="review", message=f"diagnosis 심사에 필요한 서류 누락: {reason}")
        ],
    )
    return notices.request_info("CLM-1", answer(outcome="REQUEST_INFO", note=note), before, now=NOW), before


def test_the_hold_notice_says_what_is_missing_and_until_when():
    notice, _ = held(reason="diagnosis, surgery")
    assert notice.respond_by == "2026-11-16" and "2026-11-16까지" in notice.body and "14일" in notice.body
    assert "진단서" in notice.body and "수술확인서" in notice.body and "진단서를 보내 주세요" in notice.body
    assert notice.kind == "request_info" and "CLM-1" in notice.subject


def test_a_decision_notice_carries_the_explanation():
    notice = notices.decision("CLM-1", "APPROVE", "보험금 150,000원을 지급합니다.", now=NOW)
    assert (
        notice.kind == "decision"
        and "지급 안내" in notice.subject
        and notice.body.startswith("보험금 150,000원")
        and notice.respond_by is None
    )


def run_record(deadline="2026-11-16", state="completed"):
    notice, _ = held()
    result = {
        "decision": {"reasons": [{"code": "info_requested"}]},
        "human": {"reviewer": "r"},
        "notices": [notice.model_dump(mode="json") | {"respond_by": deadline}],
    }
    claim = {
        "case_id": "C1",
        "claim_id": "CLM-1",
        "policy": {
            "policy_id": "P",
            "insured_id": "I",
            "product_id": "HLTH-A",
            "effective_from": "2025-01-01",
            "effective_to": "2030-01-01",
        },
        "requested": ["diagnosis"],
        "document_ids": ["d1"],
        "submitted_at": "2026-10-01",
    }
    return {"id": "run-1", "state": state, "request": {"kind": "claim", "claim": claim}, "result": result}


CASE = {"documents": [{"id": "d1", "kind": "admission"}], "supplementary_documents": [{"id": "s1", "kind": "diagnosis"}]}


def test_documents_are_accepted_through_the_last_day_and_refused_after_it():
    assert plan(CASE, run_record(), ["s1"], today=date(2026, 11, 16)).document_ids == ["d1", "s1"]
    with pytest.raises(ResubmitError, match="2026-11-16") as late:
        plan(CASE, run_record(), ["s1"], today=date(2026, 11, 17))
    assert late.value.conflict


def test_a_hold_from_before_notices_existed_does_not_expire():
    old = run_record()
    del old["result"]["notices"]
    assert notices.held_deadline(old["result"]) is None
    assert plan(CASE, old, ["s1"], today=date(2030, 1, 1)).document_ids == ["d1", "s1"]


def test_a_garbled_deadline_does_not_block_the_claimant():
    assert notices.held_deadline(run_record(deadline="soon")["result"]) is None
