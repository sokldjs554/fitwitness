"""The real-model trajectory evaluation without a model: the cases, the ledger and the path checks (``evaluation/agent_trajectory.py``)."""

from decimal import Decimal
from pathlib import Path

import pytest

from fitwitness.evaluation import agent_trajectory as A

ROOT = Path("var/corpus")
needs_corpus = pytest.mark.skipif(not (ROOT / "manifest.json").exists(), reason="corpus not generated")


@needs_corpus
def test_every_test_family_gets_one_query_and_a_verdict_per_seeded_revision():
    cases = A.family_cases(ROOT)
    assert [c["family_id"] for c in cases] == ["FW-F003", "FW-F012", "FW-F014", "FW-F015", "FW-F018", "FW-F029"]
    for c in cases:
        assert c["query"].endswith("mm SUS304") and str(c["need"]) in c["query"]
        assert set(c["expected"]) == set(c["revision_ids"]) and len(c["expected"]) == 5
        assert sorted(c["expected"].values()) == ["match", "match", "mismatch", "mismatch", "unknown"]
    assert cases[1]["query"].startswith("브래킷 구멍 간격") and cases[0]["query"].startswith("하우징 너비")


def test_the_ledger_never_lets_a_run_start_that_could_overrun_the_ceiling():
    ledger = A.Ledger(Decimal("0.10"))
    cap = Decimal("0.04")
    assert ledger.fits(cap)
    ledger.reserve(cap)
    assert ledger.fits(cap) and not ledger.fits(Decimal("0.07"))  # 0.04 reserved + 0.07 > 0.10
    charged = ledger.settle(cap, A.Charged(Decimal("0.008"), Decimal("0.002")))  # a reservation left behind is charged too
    assert charged == Decimal("0.010") and ledger.spent == Decimal("0.010") and ledger.reserved == 0
    ledger.reserve(cap)
    ledger.settle(cap, A.Charged(Decimal("0.04"), Decimal(0)))
    ledger.reserve(cap)
    ledger.settle(cap, A.Charged(Decimal("0.03"), Decimal(0)))
    assert ledger.spent == Decimal("0.080") and not ledger.fits(cap), "0.08 spent + 0.04 would pass 0.10"


def _events(*kinds_and_payloads):
    return [{"run_id": "r", "seq": i, "kind": k, "payload": p, "timestamp": "t"} for i, (k, p) in enumerate(kinds_and_payloads)]


def test_path_checks_see_an_uninspected_candidate_behind_a_wrong_verdict():
    expected = {"a": "match", "b": "mismatch", "c": "unknown"}
    events = _events(
        ("queued", {}),
        ("started", {}),
        ("intent", {}),
        ("retrieved", {"candidates": [{"revision_id": x} for x in "abc"]}),
        ("agent_plan", {"role": "planner", "operations": [1, 2], "stop": False}),
        ("tool", {"name": "query_dimensions", "arguments": {"revision_id": "a", "fields": ["width"]}}),
        ("tool", {"name": "query_dimensions", "arguments": {"revision_id": "b", "fields": ["width"]}}),
        ("verified", {"match": 1, "mismatch": 1, "unknown": 1}),
        ("agent_plan", {"role": "challenger", "operations": [], "stop": True}),
        ("verified", {"match": 1, "mismatch": 1, "unknown": 1}),
        ("completed", {"state": "completed"}),
    )
    checks = A.path_checks(events, expected, {"a": "match", "b": "mismatch", "c": "unknown"}, "fitwitness")
    assert checks["coverage_before_verdict"] == pytest.approx(2 / 3, abs=1e-3) and checks["uninspected"] == ["c"]
    assert checks["roles"] == ["planner", "challenger"] and checks["challenger_turn"] and checks["stop"] == "plan_stop"
    assert checks["violations"] == [] and checks["failures"] == {}
    # the same path with a verdict the labels contradict on the candidate nobody looked up
    wrong = A.path_checks(events, {**expected, "c": "match"}, {"a": "match", "b": "mismatch", "c": "unknown"}, "fitwitness")
    assert wrong["failures"] == {"c": {"expected": "match", "actual": "unknown", "class": "not_inspected"}}
    missing = A.path_checks(events[:4], expected, {}, "react")
    assert {f["class"] for f in missing["failures"].values()} == {"run_incomplete"}


def test_a_verdict_before_any_lookup_and_a_repeated_lookup_are_violations():
    events = _events(
        ("intent", {}),
        ("retrieved", {"candidates": [{"revision_id": "a"}]}),
        ("verified", {}),
        ("tool", {"name": "query_dimensions", "arguments": {"revision_id": "a", "fields": ["width"]}}),
        ("tool", {"name": "query_dimensions", "arguments": {"revision_id": "a", "fields": ["width"]}}),
        ("budget_stop", {"reason": "x"}),
        ("completed", {"state": "completed"}),
    )
    checks = A.path_checks(events, {"a": "match"}, {"a": "match"}, "react")
    text = " ".join(checks["violations"])
    assert "verdict before the first lookup" in text and "same lookup ran twice" in text and "budget_stop" in text
    assert checks["stop"] == "budget_stop"


def test_the_summary_counts_failure_classes_and_cost_per_mode():
    base = dict(
        total=2,
        state="completed",
        coverage_before_verdict=1.0,
        challenger_turn=False,
        violations=[],
        stop="plan_stop",
        model_calls=1,
        tool_calls=5,
        model_errors=[],
        cost_usd=0.004,
        latency_ms=100.0,
        actual={"a": "match", "b": "mismatch"},
        expected={"a": "match", "b": "mismatch"},
    )
    rows = [
        {**base, "provider": "rules", "mode": "fixed", "family_id": "F1", "correct": 2, "failures": {}, "cost_usd": 0.0},
        {
            **base,
            "provider": "anthropic",
            "mode": "react",
            "family_id": "F1",
            "correct": 1,
            "failures": {"b": {"class": "not_inspected"}},
            "actual": {"a": "match", "b": "unknown"},
        },
        {**base, "provider": "anthropic", "mode": "react", "family_id": "F1", "correct": 2, "failures": {}, "coverage_before_verdict": 0.8},
    ]
    s = A.summarise(rows, A.Ledger(Decimal("0.5")))
    assert s["by_mode"]["rules"]["accuracy"] == 1.0 and s["by_mode"]["react"]["accuracy"] == 0.75
    assert s["by_mode"]["react"]["failure_classes"]["not_inspected"] == 1 and s["by_mode"]["react"]["cost_usd"] == 0.008
    assert s["by_mode"]["react"]["coverage_before_verdict"] == 0.9 and s["by_mode"]["react"]["agreement_with_rules"] == 0.75


def test_a_provider_that_refuses_the_first_call_stops_the_batch_and_a_real_run_does_not():
    refused = {
        "provider": "anthropic",
        "state": "failed",
        "model_calls": 1,
        "input_tokens": 0,
        "output_tokens": 0,
        "error": "AnthropicInvalidRequestError: Error code: 400 - credit balance is too low",
    }
    assert A.rejected(refused).startswith("provider rejected")
    assert A.rejected({**refused, "provider": "rules"}) is None
    assert A.rejected({**refused, "state": "completed"}) is None
    assert (
        A.rejected({**refused, "input_tokens": 2012, "output_tokens": 418, "error": "ValueError: model output failed schema validation"})
        is None
    )


@needs_corpus
def test_the_harder_workspace_holds_the_whole_split_and_the_labels_follow_kind_value_and_material():
    cases = A.family_cases(ROOT, workspace="split", materials=("SUS304", "AL6061"), top_k=12)
    assert len(cases) == 12 and {c["material"] for c in cases} == {"SUS304", "AL6061"}
    base, other = cases[0], cases[1]
    assert (base["case_id"], other["case_id"]) == ("FW-F003:SUS304", "FW-F003:AL6061") and other["query"].endswith("71mm AL6061")
    assert (
        len(base["expected"]) == 30
        and set(base["revision_ids"]) < set(base["expected"])
        and base["workspace_families"] == sorted({c["family_id"] for c in cases})
    )
    own = lambda c: sorted(c["expected"][r] for r in c["revision_ids"])  # noqa: E731
    assert own(base) == ["match", "match", "mismatch", "mismatch", "unknown"]
    assert own(other) == ["match", "mismatch", "mismatch", "mismatch", "unknown"], "the AL6061 query matches the one aluminium drawing"
    assert {v for k, v in base["expected"].items() if k not in base["revision_ids"]} <= {"mismatch", "unknown"}, (
        "no other family satisfies it"
    )
    assert A.search_for(base).top_k == 12 and A.search_for(A.family_cases(ROOT)[0]).top_k == 6
    assert A.label("bracket", {"hole_spacing": 64, "material": "SUS304"}, "flange", "hole_spacing", 64, "SUS304") == "mismatch"


def test_a_drawing_the_search_never_returned_is_the_searchs_miss_and_repeats_are_counted_as_cases():
    events = _events(("retrieved", {"candidates": [{"revision_id": "a"}]}), ("completed", {"state": "completed"}))
    case = {"revision_ids": ["a", "b"], "expected": {"a": "match", "b": "match", "z": "mismatch", "y": "mismatch"}}
    got = A.scored(case, events, {"z": "mismatch"})
    assert got == {"a": "match", "b": "match", "z": "mismatch"}, (
        "own drawings, retrieved ones and ones given a verdict are scored; the rest are not"
    )
    checks = A.path_checks(events, got, {"z": "mismatch"}, "react")
    assert checks["failures"]["b"]["class"] == "not_retrieved" and checks["failures"]["a"]["class"] == "run_incomplete"
    base = dict(
        provider="anthropic",
        mode="react",
        state="completed",
        total=2,
        coverage_before_verdict=1.0,
        challenger_turn=False,
        violations=[],
        stop="plan_stop",
        model_calls=1,
        tool_calls=3,
        model_errors=[],
        cost_usd=0.01,
        latency_ms=1.0,
        failures={},
        expected={"a": "match"},
    )
    rows = [
        {**base, "case_id": "F1:SUS304", "family_id": "F1", "repeat": 0, "correct": 2, "actual": {"a": "match"}},
        {**base, "case_id": "F1:SUS304", "family_id": "F1", "repeat": 1, "correct": 1, "actual": {"a": "unknown"}},
        {**base, "case_id": "F2:SUS304", "family_id": "F2", "repeat": 0, "correct": 2, "actual": {"a": "match"}},
        {**base, "case_id": "F2:SUS304", "family_id": "F2", "repeat": 1, "correct": 2, "actual": {"a": "match"}},
    ]
    m = A.summarise(rows, A.Ledger(Decimal("0.5")))["by_mode"]["react"]
    assert (m["cases"], m["repeats"], m["runs_fully_correct"], m["cases_whose_verdicts_changed"]) == (2, 2, 3, 1)
    assert m["pass_k"]["cases_all_correct"] == 1 and m["pass_k"]["pass_power_k"] == 0.5 and m["accuracy"] == 0.875
