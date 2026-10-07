"""The trajectory checks must see a wrong road, not only a wrong destination."""
import copy
from fitwitness.evaluation.trajectory import claim_invariants, divergence, drawing_invariants, signature, token

PAID = ["intake", "tool:extract_documents", "extracted", "validated", "adjudicated:APPROVE", "challenged:APPROVE", "payout:paid", "explained", "completed:APPROVE"]
PARKED = ["intake", "tool:extract_documents", "extracted", "validated", "adjudicated:REVIEW", "challenged:REVIEW", "waiting_input"]
GOLD_PAY = {"gold": {"outcome": "APPROVE", "total_amount": 210000}}
GOLD_DENY = {"gold": {"outcome": "DENY", "total_amount": 0}}
GOLD_REVIEW = {"gold": {"outcome": "REVIEW", "total_amount": 90000}}


def run(sig, ledger=(), state="completed", proposed=None, need=1):
    return {"state": state, "signature": list(sig), "ledger": list(ledger), "proposed": proposed, "approvals_required": need}


def test_tokens_keep_the_path_and_drop_the_detail():
    assert token({"kind": "adjudicated", "payload": {"outcome": "DENY", "total_amount": 5}}) == "adjudicated:DENY"
    assert token({"kind": "tool", "payload": {"name": "query_dimensions", "latency_ms": 3.2}}) == "tool:query_dimensions"
    assert token({"kind": "payout", "payload": {"status": "paid", "amount": 1}}) == "payout:paid"
    events = [{"kind": "queued"}, {"kind": "started"}, {"kind": "intake", "payload": {"claim_id": "x"}}]
    assert signature(events) == ["intake"]


def test_divergence_names_the_first_difference():
    assert divergence(["a", "b", "c"], ["a", "b", "c"]) == {}
    assert divergence(["a", "b", "c"], ["a", "x", "c"]) == {"at": 1, "expected": "b", "actual": "x"}
    assert divergence(["a", "b"], ["a", "b", "c"]) == {"at": 2, "expected": "(end)", "actual": "c"}


def test_a_correct_paid_claim_and_its_rerun_are_clean():
    first = run(PAID, [("CLM-1", 210000)])
    rerun = run(PAID[:4] + ["adjudicated:DENY", "challenged:DENY", "payout:skipped", "explained", "completed:DENY"], [("CLM-1", 210000)])
    assert claim_invariants({}, GOLD_PAY, first, {}, rerun) == []


def test_each_wrong_road_is_caught():
    ok = run(PAID, [("CLM-1", 210000)])
    swapped = PAID[:5] + ["payout:paid", "challenged:APPROVE"] + PAID[7:]
    cases = {
        "payout before the challenge step": (run(swapped, [("CLM-1", 210000)]), {}, None, GOLD_PAY),
        "a node is missing": (run([t for t in PAID if t != "validated"], [("CLM-1", 210000)]), {}, None, GOLD_PAY),
        "two payouts": (run(PAID + ["payout:paid"], [("CLM-1", 210000), ("CLM-1", 210000)]), {}, None, GOLD_PAY),
        "money moved while a reviewer was asked": (run(PARKED, [("CLM-1", 90000)], state="waiting_input", proposed=90000), {}, None, GOLD_REVIEW),
        "an event that has no business on a clean run": (run(PAID[:6] + ["retry_scheduled"] + PAID[6:], [("CLM-1", 210000)]), {}, None, GOLD_PAY),
        "paid where gold says deny": (ok, {}, None, GOLD_DENY),
        "paid a different amount": (run(PAID, [("CLM-1", 999)]), {}, None, GOLD_PAY),
        "a rerun changed the ledger": (ok, {}, run(PAID, [("CLM-1", 210000), ("CLM-1", 210000)]), GOLD_PAY),
    }
    for name, (first, answers, rerun, gold) in cases.items():
        assert claim_invariants({}, gold, first, answers, rerun), name
    assert claim_invariants({}, GOLD_PAY, ok, {}, None) == []


def test_reviewer_answers_are_applied_once_and_only_as_given():
    parked = run(PARKED, [], state="waiting_input", proposed=90000)
    approved = run(PARKED + ["resumed_by_human", "human_review:APPROVE", "payout:paid", "explained", "completed:APPROVE"], [("CLM-1", 90000)])
    denied = run(PARKED + ["resumed_by_human", "human_review:DENY", "payout:skipped", "explained", "completed:DENY"], [])
    assert claim_invariants({}, GOLD_REVIEW, parked, {"approve": approved, "deny": denied}, None) == []
    # approval that pays nothing, or twice, or a denial that pays
    no_pay = run(approved["signature"], [])
    twice = run(approved["signature"], [("CLM-1", 90000), ("CLM-1", 90000)])
    paying_denial = run(denied["signature"], [("CLM-1", 90000)])
    assert claim_invariants({}, GOLD_REVIEW, parked, {"approve": no_pay}, None)
    assert claim_invariants({}, GOLD_REVIEW, parked, {"approve": twice}, None)
    assert claim_invariants({}, GOLD_REVIEW, parked, {"deny": paying_denial}, None)
    # nothing owed: approving must not write a ledger row
    zero = run(PARKED, [], state="waiting_input", proposed=0)
    zero_approved = run(PARKED + ["resumed_by_human", "human_review:APPROVE", "payout:skipped", "explained", "completed:APPROVE"], [])
    assert claim_invariants({}, GOLD_REVIEW, zero, {"approve": zero_approved}, None) == []


def _tool(revision, fields=("hole_spacing", "material")):
    return {"kind": "tool", "payload": {"name": "query_dimensions", "arguments": {"revision_id": revision, "fields": list(fields)}}}


def _drawing(tools):
    return [{"kind": "intent"}, {"kind": "retrieved", "payload": {"candidates": [{} for _ in tools]}}, *tools, {"kind": "verified"}, {"kind": "completed"}]


def test_drawing_path_checks():
    assert drawing_invariants("rules", _drawing([_tool("a"), _tool("b")]), "completed") == []
    assert drawing_invariants("rules", _drawing([_tool("a"), _tool("a")]), "completed"), "the same lookup twice"
    after = _drawing([_tool("a")])
    after.insert(2, {"kind": "retrieved", "payload": {"candidates": [{}]}})
    short = _drawing([_tool("a"), _tool("b")])
    short[1]["payload"]["candidates"].append({})  # a candidate nobody looked up
    assert drawing_invariants("rules", short, "completed"), "a candidate with no lookup"
    early = copy.deepcopy(_drawing([_tool("a")]))
    early.insert(2, {"kind": "verified"})
    assert drawing_invariants("rules", early, "completed"), "a verdict before the first lookup"
    assert drawing_invariants("rules", _drawing([_tool("a")]) + [{"kind": "failed"}], "failed")


def test_a_senior_claim_is_paid_only_after_two_people_were_asked():
    parked = run(PARKED, [], state="waiting_input", proposed=31_600_000, need=2)
    two_asks = PARKED + ["resumed_by_human", "resumed", "waiting_input", "resumed_by_human", "resumed", "human_review:APPROVE", "payout:paid", "explained", "completed:APPROVE"]
    ok = run(two_asks, [("CLM-1", 31_600_000)])
    gold = {"gold": {"outcome": "REVIEW", "total_amount": 31_600_000}}
    assert claim_invariants({}, gold, parked, {"approve": ok}, None) == []
    one_ask = run(PARKED + ["resumed_by_human", "resumed", "human_review:APPROVE", "payout:paid", "explained", "completed:APPROVE"], [("CLM-1", 31_600_000)])
    assert any("needs 2 people" in v for v in claim_invariants({}, gold, parked, {"approve": one_ask}, None))
    early = run(PARKED + ["resumed_by_human", "resumed", "payout:paid", "waiting_input", "resumed_by_human", "resumed", "human_review:APPROVE", "explained", "completed:APPROVE"], [("CLM-1", 31_600_000)])
    assert any("before the last required approval" in v for v in claim_invariants({}, gold, parked, {"approve": early}, None))
    denied_twice = run(PARKED + ["resumed_by_human", "resumed", "waiting_input", "resumed_by_human", "human_review:DENY", "payout:skipped", "explained", "completed:DENY"], [])
    assert any("one refusal is final" in v for v in claim_invariants({}, gold, parked, {"deny": denied_twice}, None))
