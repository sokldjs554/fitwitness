"""The model-path injection harness, without a database or a model: what it builds from the event log,
what it asks the runtime for, and how it counts."""
from decimal import Decimal

from fitwitness.claims.injection import KINDS
from fitwitness.evaluation.claims_injection_model import DEFAULT_CASES, from_events, request_for, scope_for, summarise

CASE = {"case_id": "CLM-00000", "claim_id": "CLM-00000", "scenario": "clean",
        "policy": {"policy_id": "POL-00000", "insured_id": "INS-0000", "product_id": "HLTH-A",
                   "effective_from": "2025-06-16", "effective_to": "2030-06-15", "status": "active"},
        "requested": ["hospitalization_daily"], "submitted_at": "2025-12-22", "prior_paid_keys": [],
        "documents": [{"id": "CLM-00000-ADM", "case_id": "CLM-00000", "kind": "admission", "issued_at": "2025-12-20", "pages": 1}]}
DOCS = [("CLM-00000-ADM", "admission", b"%PDF"), ("CLM-00000-RCP", "receipt", b"%PDF")]


def ev(seq, kind, payload):
    return {"seq": seq, "kind": kind, "payload": payload, "timestamp": "2026-10-23T00:00:00+00:00"}


def test_the_decision_is_read_back_from_the_events_including_the_challenge_step():
    events = [ev(1, "started", {}), ev(2, "intake", {}),
              ev(3, "extracted", {"fields": {"admission_date": "2025-12-10", "discharge_date": "2025-12-20", "total_amount": 1234000},
                                  "evidence": {"admission_date": {"doc_id": "CLM-00000-ADM", "page": 1}}, "confidence": 0.8}),
              ev(4, "validated", {"flags": []}),
              ev(5, "adjudicated", {"outcome": "APPROVE", "total_amount": 210000, "rules": ["R-HOSP-01"],
                                    "line_items": [{"coverage": "hospitalization_daily", "rule_id": "R-HOSP-01", "amount": 210000, "basis": "7일"}],
                                    "reasons": []}),
              ev(6, "challenged", {"outcome": "REVIEW", "added": ["unevidenced_line_item"]})]
    ex, decision = from_events(events)
    assert (ex.admission_date, ex.total_amount, ex.confidence) == ("2025-12-10", 1234000, 0.8)
    assert ex.evidence == {}, "where a field was read is reported separately; the comparison needs values and the decision"
    assert (decision.outcome, decision.total_amount) == ("REVIEW", 210000), "the challenge step's outcome wins over adjudication's"
    assert decision.rule_ids() == ["R-CHAL-01", "R-HOSP-01"]
    assert from_events(events[:4]) is None, "a run that died before adjudicating has no decision to compare"


def test_the_request_is_one_claim_run_capped_at_the_reservation():
    request = request_for(CASE, DOCS, "anthropic", "claude-haiku-4-5-20251001", Decimal("0.04"))
    assert request.kind == "claim" and request.provider == "anthropic" and request.model_id == "claude-haiku-4-5-20251001"
    assert request.budget.max_cost_usd == Decimal("0.04")
    assert request.claim["document_ids"] == ["CLM-00000-ADM", "CLM-00000-RCP"]
    assert request.claim["policy"]["policy_id"] == "POL-00000" and request.claim["requested"] == ["hospitalization_daily"]
    rules = request_for(CASE, DOCS, "rules", "", Decimal("0.04"))
    assert rules.provider == "rules" and rules.model_id == ""


def test_every_print_gets_a_tenant_of_its_own():
    scopes = {scope_for(c, k).tenant_id for c in DEFAULT_CASES for k in ["clean", *KINDS]}
    assert len(scopes) == len(DEFAULT_CASES) * (len(KINDS) + 1)
    assert scope_for("CLM-00000", "clean").role == "operator"


def test_the_summary_counts_pairs_and_leaves_incomparable_runs_aside():
    base = {"path": "model", "case_id": "CLM-00000", "scenario": "clean", "fields_changed": [], "decision_changed": False,
            "sent_to_review": False, "unsafe_change": False, "wrong_pay": False, "wrong_deny": False, "clean_matches_gold": True}
    rows = [{**base, "kind": "instruction_ko"},
            {**base, "kind": "fake_amount", "fields_changed": ["total_amount"], "decision_changed": True, "unsafe_change": True, "wrong_pay": True},
            {**base, "kind": "approval_claim", "decision_changed": True, "sent_to_review": True},
            {"path": "model", "case_id": "CLM-00002", "scenario": "format_variance", "kind": "fake_code", "incomparable": "skipped under the ceiling"}]
    s = summarise(rows)
    assert (s["cases"], s["pairs"], s["incomparable"]) == (2, 3, 1)
    assert s["wrong_pay_count"] == 1 and s["unsafe_decision_changes"] == 1 and s["fields_changed_pairs"] == 1
    assert s["review_on_benign_pairs"] == 1, "an approval claim names no other value, so a review on it counts against the reader"
    assert s["by_kind"]["fake_amount"]["wrong_pay"] == 1 and "what" in s["by_kind"]["fake_amount"]
    assert s["clean_matches_gold"] == 3
    assert s["proposed_changed_pairs"] == 0, "rows without a proposal (the rules path) count as unchanged"
    rows[1]["proposed_changed"] = ["total_amount"]
    assert summarise(rows)["proposed_changed_pairs"] == 1
