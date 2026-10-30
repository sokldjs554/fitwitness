"""Sign-off rules: who may approve a claim that stopped for a person."""

import pytest
from fitwitness.claims.models import ClaimReview
from fitwitness.claims.policy import PRODUCTS
from fitwitness.claims.review import NOTE_MIN_CHARS, ReviewRuleError, check_answer, requirements, same_person

CANCER, HEALTH = PRODUCTS["CANCER-B"], PRODUCTS["HLTH-A"]


def answer(outcome="APPROVE", reviewer="김심사", note=""):
    return ClaimReview(outcome=outcome, reviewer=reviewer, note=note)


def test_the_authority_matrix_is_data_on_the_product():
    assert CANCER.second_approval_above == 30_000_000 and HEALTH.second_approval_above == 10_000_000
    assert CANCER.second_approval_above > CANCER.auto_approve_limit, "a person signs between the two limits, two people above"


def test_amount_decides_the_tier_and_the_threshold_itself_is_not_above_it():
    assert requirements(CANCER, 30_000_000)["tier"] == "standard"
    senior = requirements(CANCER, 30_000_001)
    assert senior == {"tier": "senior", "approvals_required": 2, "note_required": True, "escalated": False, "threshold": 30_000_000}
    assert requirements(HEALTH, 12_230_000)["approvals_required"] == 2 and requirements(HEALTH, 7_000_000)["approvals_required"] == 1


def test_a_missed_deadline_makes_any_claim_senior():
    late = requirements(HEALTH, 90_000, escalated=True)
    assert late["tier"] == "senior" and late["approvals_required"] == 2 and late["escalated"]


def test_a_product_without_a_threshold_never_needs_two_people():
    bare = CANCER.model_copy(update={"second_approval_above": None})
    assert requirements(bare, 10**9)["approvals_required"] == 1
    assert requirements(bare, 10**9, escalated=True)["approvals_required"] == 2


def test_senior_approval_needs_a_reason_but_a_denial_does_not():
    waiting = {"tier": "senior", "step": 1}
    with pytest.raises(ReviewRuleError):
        check_answer(waiting, answer(note="ok"))
    with pytest.raises(ReviewRuleError):
        check_answer(waiting, answer(note=" " * 20))  # blanks are not a reason
    check_answer(waiting, answer(note="x" * NOTE_MIN_CHARS))
    check_answer(waiting, answer("DENY"))
    check_answer({"tier": "standard", "step": 1}, answer())  # a standard claim needs no reason
    with pytest.raises(ReviewRuleError):
        check_answer({"tier": "standard", "step": 1}, answer(note="짧음"), escalated=True)


def test_the_second_approver_is_a_different_person():
    waiting = {"tier": "senior", "step": 2, "first_reviewer": "김심사"}
    for same in ("김심사", " 김심사 ", "김심사\u3000"):  # spacing around a name does not make a second person
        with pytest.raises(ReviewRuleError):
            check_answer(waiting, answer(reviewer=same, note="사유를 충분히 적었습니다"))
    check_answer(waiting, answer(reviewer="박팀장", note="사유를 충분히 적었습니다"))
    assert same_person("Kim Reviewer", "kim  reviewer") and not same_person("a", "b") and not same_person("김심사", "김 심사")
    # a refusal does not need a different person to be valid
    with pytest.raises(ReviewRuleError):
        check_answer(waiting, answer("DENY", reviewer="김심사"))


def test_a_request_for_documents_needs_to_say_what_is_missing_and_is_final_at_either_step():
    for waiting in ({"tier": "standard", "step": 1}, {"tier": "senior", "step": 1}):
        with pytest.raises(ReviewRuleError):
            check_answer(waiting, answer("REQUEST_INFO", note="  "))  # a blank is not a list of missing documents
        check_answer(waiting, answer("REQUEST_INFO", note="진단서 원본 필요"))  # no 10-character reason, no second person
    second = {"tier": "senior", "step": 2, "first_reviewer": "김심사"}
    check_answer(second, answer("REQUEST_INFO", reviewer="박팀장", note="수술기록 보완"))
    with pytest.raises(ReviewRuleError):  # the second answer still has to come from a different person
        check_answer(second, answer("REQUEST_INFO", reviewer="김심사", note="수술기록 보완"))


def test_a_reviewer_can_lower_the_amount_to_approve_but_never_raise_it():
    # the tier (one person or two) was chosen on the proposed amount, so a raise would pay what the required people did not sign
    waiting = {"tier": "standard", "step": 1, "total_amount": 210_000}
    check_answer(waiting, ClaimReview(outcome="APPROVE", reviewer="김심사", total_amount=200_000))
    check_answer(waiting, ClaimReview(outcome="APPROVE", reviewer="김심사", total_amount=210_000))
    with pytest.raises(ReviewRuleError):
        check_answer(waiting, ClaimReview(outcome="APPROVE", reviewer="김심사", total_amount=9_000_000_000))
    with pytest.raises(ReviewRuleError):
        check_answer(waiting, ClaimReview(outcome="APPROVE", reviewer="김심사", total_amount=210_001))
    check_answer(waiting, ClaimReview(outcome="DENY", reviewer="김심사", total_amount=9_000_000_000))  # a refusal pays nothing
    with pytest.raises(ValueError):  # beyond any amount the ledger column can hold
        ClaimReview(outcome="APPROVE", reviewer="김심사", total_amount=10**30)
