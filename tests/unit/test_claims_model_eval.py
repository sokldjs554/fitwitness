"""The model-path claims evaluation's scoring and summary, without a database or a model."""
from fitwitness.claims.models import Extraction
from fitwitness.evaluation.claims_model import SPLITS, score, summarise

CASE = {"case_id": "CLM-00001", "scenario": "clean"}
GOLD = {"gold": {"outcome": "APPROVE", "total_amount": 210000}, "truth": {"admission_date": "2025-12-07", "total_amount": 3884730, "insured_name": "박지우"},
        "visible_fields": ["admission_date", "total_amount", "insured_name"]}


def test_a_reader_is_scored_like_the_claims_evaluation_and_a_failed_run_scores_nothing():
    ex = Extraction(admission_date="2025-12-07", total_amount=3884730, insured_name="박 지우")
    s = score(CASE, GOLD, ex, "APPROVE", 210000, None)
    assert s["correct"] and s["auto_paid"] and not s["wrong_pay"] and not s["wrong_deny"]
    assert s["hits"] == {"admission_date": True, "total_amount": True, "insured_name": False}, "spaces in a name count only in scan mode"
    assert score(CASE, GOLD, ex, "APPROVE", 210000, "medium")["hits"]["insured_name"] is True
    none = score(CASE, GOLD, None, None, None, None)
    assert not none["correct"] and not none["wrong_pay"] and all(v is False for v in none["hits"].values())
    assert score(CASE, GOLD, ex, "APPROVE", 99000000, None)["wrong_pay"], "a different amount is a wrong payment"
    assert score(CASE, GOLD, ex, "DENY", 0, None)["wrong_deny"]


def test_the_summary_counts_both_paths_and_the_model_paths_run_facts():
    def row(case_id, scenario, gold, m_pred, m_amt, r_pred, r_amt, state="completed", rules=None, dropped=None):
        hits = {"admission_date": True, "total_amount": m_pred != "REVIEW"}
        mk = lambda pred, amt: {"predicted": pred, "amount": amt, "hits": hits, "correct": pred == gold[0] and amt == gold[1],  # noqa: E731
                                "wrong_pay": pred == "APPROVE" and (gold[0] != "APPROVE" or amt != gold[1]), "wrong_deny": pred == "DENY" and gold[0] != "DENY",
                                "auto_paid": pred == "APPROVE" and gold[0] == "APPROVE" and amt == gold[1], "rules": rules or [], "confidence": 1.0}
        return {"case_id": case_id, "scenario": scenario, "gold": gold[0], "gold_amount": gold[1], "rules": mk(r_pred, r_amt), "model": mk(m_pred, m_amt),
                "agree": (m_pred, m_amt) == (r_pred, r_amt),
                "run": {"state": state, "error": None if state != "failed" else "boom", "latency_ms": 2000.0, "model_calls": 1, "input_tokens": 2000,
                        "output_tokens": 200, "cost_usd": 0.003, "charged_usd": 0.003, "model_errors": [], "repairs": 0, "proposed": {}, "dropped": dropped or [],
                        "grounded_fields": [], "run_id": "r"}}
    rows = [row("CLM-00001", "clean", ("APPROVE", 210000), "APPROVE", 210000, "APPROVE", 210000),
            row("CLM-00003", "clean", ("APPROVE", 150000), "REVIEW", 150000, "APPROVE", 150000, state="waiting_input", rules=["R-CHAL-02"], dropped=["total_amount"]),
            row("CLM-00005", "duplicate", ("DENY", 0), None, None, "DENY", 0, state="failed")]
    m, r = summarise(rows, "model"), summarise(rows, "rules")
    assert (m["cases"], m["completed_or_waiting"], m["failed"]) == (3, 2, 1)
    assert m["decision_accuracy"] == round(1 / 3, 4) and r["decision_accuracy"] == 1.0
    assert m["review_rate"] == round(1 / 3, 4) and m["auto_rate"] == 0.5 and r["auto_rate"] == 1.0
    assert m["ungrounded_reviews"] == 1 and m["fields_dropped_by_grounding"] == 1 and m["cost_usd"] == 0.009
    assert m["by_scenario"]["clean"] == {"cases": 2, "correct": 1, "wrong_pay": 0, "review": 1}
    assert "cost_usd" not in r, "the rules reader costs nothing to run"
    assert SPLITS["test"](1) and not SPLITS["test"](2) and SPLITS["dev"](2) and SPLITS["all"](7)
