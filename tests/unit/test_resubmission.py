"""Documents sent after a request for them: the corpus, the evaluation and the planning of the continuing run."""

import json

import pytest

from fitwitness.claims import resubmit
from fitwitness.claims.evaluate import evaluate_resubmission
from fitwitness.claims.models import ClaimRequest, Policy
from fitwitness.claims.synth import generate_cases


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("resub")
    cases = generate_cases(root, n=22, seed=11)  # two of every scenario
    return root, cases, json.loads((root / "manifest.json").read_text())


def test_only_the_scenarios_that_ask_for_documents_have_documents_waiting(corpus):
    root, cases, manifest = corpus
    waiting = {c.scenario for c in cases if c.supplementary}
    assert waiting == {"missing_doc", "grade_missing"}
    for case in cases:
        for doc in case.supplementary:
            assert (
                doc.id.endswith("-S")
                and (root / case.case_id / f"{doc.id}.pdf").exists()
                and (root / case.case_id / f"{doc.id}.png").exists()
            )
            assert doc.id not in {d.id for d in case.documents}
        assert (case.gold_after_supplement is not None) == bool(case.supplementary)
    assert not list(root.glob("*/_supplement"))  # the scratch folder is gone


def test_a_claim_held_for_documents_reaches_the_rules_decision_once_they_arrive(corpus):
    root, _, _ = corpus
    result = evaluate_resubmission(root)
    s = result["summary"]
    assert s["cases"] >= 3 and s["held_before"] >= 3
    assert s["paid_before"] == 0  # nothing is paid before the documents are there
    assert s["correct_after"] == s["held_before"] and s["wrong_pay_after"] == 0
    assert s["paid_after"] + s["review_after"] == s["held_before"]
    for row in result["rows"]:
        assert row["before"] != "APPROVE", row["case_id"]


def _run(request: ClaimRequest, **over):
    return {
        "id": "run-1",
        "state": "completed",
        "request": {"kind": "claim", "claim": request.model_dump(mode="json")},
        "result": {
            "decision": {"reasons": [{"code": "info_requested", "rule_id": "R-HUMAN-02"}]},
            "human": {"reviewer": "심사역 김", "note": "서류 요청"},
        },
        **over,
    }


def test_the_continuing_run_replaces_a_document_of_the_same_kind_and_keeps_the_rest(corpus):
    _, cases, manifest = corpus
    case = next(c for c in manifest["cases"] if c.get("supplementary_documents") and c["scenario"] == "grade_missing")
    request = ClaimRequest(
        case_id=case["case_id"],
        claim_id=case["claim_id"],
        policy=Policy.model_validate(case["policy"]),
        requested=case["requested"],
        document_ids=[d["id"] for d in case["documents"]],
        submitted_at=case["submitted_at"],
    )
    new = case["supplementary_documents"][0]["id"]
    planned = resubmit.plan(case, _run(request), [new])
    old = next(d["id"] for d in case["documents"] if d["kind"] == "surgery")
    assert planned.resubmission.replaced == [old] and planned.resubmission.attempt == 2 and planned.resubmission.of_run == "run-1"
    assert sorted(planned.document_ids) == sorted([d["id"] for d in case["documents"] if d["id"] != old] + [new])
    assert planned.claim_id == request.claim_id  # the same claim: the ledger key does not change
    # and a second hold of the continuing run can take a further document, counting the attempts
    again = resubmit.plan(
        {**case, "supplementary_documents": [*case["supplementary_documents"], {"id": "X-S", "kind": "receipt"}]}, _run(planned), ["X-S"]
    )
    assert again.resubmission.attempt == 3 and again.resubmission.replaced == [d["id"] for d in case["documents"] if d["kind"] == "receipt"]


def test_documents_are_refused_unless_the_run_was_held_for_them(corpus):
    _, _, manifest = corpus
    case = next(c for c in manifest["cases"] if c.get("supplementary_documents"))
    request = ClaimRequest(
        case_id=case["case_id"],
        claim_id=case["claim_id"],
        policy=Policy.model_validate(case["policy"]),
        requested=case["requested"],
        document_ids=[d["id"] for d in case["documents"]],
        submitted_at=case["submitted_at"],
    )
    waiting = case["supplementary_documents"][0]["id"]
    for state in ("waiting_input", "running", "failed"):
        with pytest.raises(resubmit.ResubmitError) as e:
            resubmit.plan(case, _run(request, state=state), [waiting])
        assert e.value.conflict
    approved = {**_run(request), "result": {"decision": {"reasons": []}, "human": None}}
    with pytest.raises(resubmit.ResubmitError):
        resubmit.plan(case, approved, [waiting])
    drawing = {**_run(request), "request": {"kind": "drawing"}}
    with pytest.raises(resubmit.ResubmitError):
        resubmit.plan(case, drawing, [waiting])
    with pytest.raises(resubmit.ResubmitError) as e:
        resubmit.plan(case, _run(request), [waiting, waiting])
    assert not e.value.conflict
    with pytest.raises(resubmit.ResubmitError):
        resubmit.plan(case, _run(request), ["not-a-waiting-document"])
