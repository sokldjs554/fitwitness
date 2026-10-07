"""Each payout rule on its own, against the policy table."""
from fitwitness.claims.models import Extraction, Policy
from fitwitness.claims.policy import PRODUCTS, adjudicate, dedupe_keys, explain, validate

POL = Policy(policy_id="POL-1", insured_id="I-1", product_id="HLTH-A", effective_from="2025-01-01", effective_to="2030-01-01")
ALL_DOCS = {"diagnosis", "admission", "surgery", "receipt"}


def ex(**kw):
    base = dict(diagnosis_code="K35.8", diagnosis_date="2025-06-01", admission_date="2025-06-01", discharge_date="2025-06-06",
                surgery_date="2025-06-02", surgery_grade=2, insured_name="김민준", hospital="한빛의료원")
    base.update(kw)
    return Extraction(**base)


def run(e, requested=("hospitalization_daily", "surgery"), docs=ALL_DOCS, prior=(), flags=(), policy=POL):
    return adjudicate(policy, PRODUCTS[policy.product_id], list(requested), e, set(docs), set(prior), list(flags))


def test_daily_and_surgery_lines_carry_rule_ids_and_amounts():
    d = run(ex())
    assert d.outcome == "APPROVE" and d.total_amount == 2 * 30_000 + 300_000
    assert d.rule_ids() == ["R-HOSP-01", "R-SURG-01"]
    assert "지급합니다" in explain(d) and "[R-SURG-01]" in explain(d)


def test_deductible_days_are_not_paid():
    d = run(ex(discharge_date="2025-06-03"), requested=("hospitalization_daily",))
    assert d.outcome == "DENY" and d.total_amount == 0 and [r.code for r in d.reasons] == ["below_deductible"]


def test_missing_document_asks_a_person_instead_of_guessing():
    d = run(ex(), docs={"diagnosis", "receipt"})
    assert d.outcome == "REVIEW" and d.total_amount == 0 and {r.code for r in d.reasons} == {"missing_documents"}


def test_exclusions_waiting_period_and_lapsed_policy_deny():
    assert run(ex(diagnosis_code="Z41.1"), requested=("surgery",)).outcome == "DENY"
    cancer = POL.model_copy(update={"product_id": "CANCER-B"})
    early = run(ex(diagnosis_code="C16.9", diagnosis_date="2025-02-01"), requested=("diagnosis",), policy=cancer)
    assert early.outcome == "DENY" and early.reasons[0].rule_id == "R-WAIT-01"
    late = run(ex(diagnosis_code="C16.9", diagnosis_date="2025-06-01"), requested=("diagnosis",), policy=cancer)
    assert late.outcome == "REVIEW" and late.total_amount == 30_000_000  # above the auto-approve limit
    lapsed = run(ex(), policy=POL.model_copy(update={"status": "lapsed"}))
    assert lapsed.outcome == "DENY" and lapsed.line_items == []


def test_duplicates_are_denied_by_dedupe_key():
    paid = run(ex())
    keys = dedupe_keys(POL, ex(), paid)
    assert keys == ["POL-1|hosp|2025-06-01", "POL-1|surg|2025-06-02"]
    again = run(ex(), prior=keys)
    assert again.outcome == "DENY" and {r.code for r in again.reasons} == {"duplicate_claim"}


def test_contradictions_and_low_confidence_route_to_review():
    flags = validate(ex(discharge_date="2025-05-30"), ALL_DOCS)
    assert "discharge_before_admission" in flags
    assert run(ex(), flags=flags).outcome == "REVIEW"
    assert run(ex(confidence=0.5)).outcome == "REVIEW"
    assert "missing_evidence" in validate(ex(), ALL_DOCS)  # populated fields without a source position


def test_a_refusal_resting_on_a_scan_goes_to_a_person_but_a_lapsed_policy_is_still_a_refusal():
    excluded = run(ex(diagnosis_code="Z41.1", scanned=True), requested=("surgery",))
    assert excluded.outcome == "REVIEW" and excluded.needs_human and "R-SCAN-01" in excluded.rule_ids()
    assert run(ex(diagnosis_code="Z41.1"), requested=("surgery",)).outcome == "DENY"  # the same claim from a text layer
    early = run(ex(diagnosis_code="C16.9", diagnosis_date="2025-02-01", scanned=True), requested=("diagnosis",), policy=POL.model_copy(update={"product_id": "CANCER-B"}))
    assert early.outcome == "REVIEW" and {"R-WAIT-01", "R-SCAN-01"} <= set(early.rule_ids())
    lapsed = run(ex(scanned=True), policy=POL.model_copy(update={"status": "lapsed"}))
    assert lapsed.outcome == "DENY"  # the contract's status is not something the scan said
    assert run(ex(scanned=True)).outcome == "APPROVE"  # paying what was read with confidence is still automatic
