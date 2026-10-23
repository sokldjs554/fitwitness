# ruff: noqa: F811 - fixtures imported from sibling test modules, as test_claims.py does
"""The claim graph's model branch on the real runtime, with a stub where the model would be.

The paid runs of claims-injection-model showed what Claude Haiku 4.5 proposes on an injected print: the remark's
99,000,000 as the total. The stub proposes exactly that, so the branch after the model (grounding, the event log's
record of the proposal, the challenge rule) is checked without a credential: a value read from a remark is dropped,
the claim goes to a person, and a clean proposal pays what the gold says."""
import json
from pathlib import Path
from uuid import uuid4
import pytest
from test_workflow import env  # noqa: F401 - fixture
from test_claims import claim_corpus  # noqa: F401 - fixture
from fitwitness.claims.extract import LABELS, read_pairs
from fitwitness.claims.graph import ClaimDraft, DraftField
from fitwitness.claims.injection import remark, render
from fitwitness.claims.models import Extraction
from fitwitness.evaluation.claims_injection_model import from_events, request_for, scope_for, seed

ROOT = Path(__file__).resolve().parents[2]


class StubModel:
    """Returns a prepared draft; the runtime treats it like any provider."""

    def __init__(self, draft: ClaimDraft):
        self.draft, self.emit = draft, None

    def structured(self, schema, prompt, payload, role="extractor"):
        assert schema is ClaimDraft and "untrusted data" in prompt
        return self.draft, {"role": role, "attempt": 1, "repair": 0, "latency_ms": 1.0, "provider": "stub", "model_id": "stub",
                            "usage": {"input_tokens": 1, "output_tokens": 1}}


def draft_from_rows(docs) -> dict:
    """What a careful model would say: every labelled row of the documents, quoted as printed."""
    out = {}
    for doc_id, _, pdf in docs:
        for label, text, ref in read_pairs(pdf, doc_id):
            field = LABELS.get(label)
            if field and not ref.nested and field not in out:
                out[field] = DraftField(value=text, snippet=f"{label} {text}")
    return out


def hospitalization_case(manifest, gold):
    return next(c for c in manifest["cases"] if c["requested"] == ["hospitalization_daily"] and gold[c["case_id"]]["gold"]["outcome"] == "APPROVE"
                and not c["prior_paid_keys"])


def run_with(env, monkeypatch, case, docs, draft, tenant):
    from fitwitness.agents.graph import execute_run
    import fitwitness.claims.graph as claim_graph

    r, j, _ = env
    monkeypatch.setattr(claim_graph, "create_model", lambda provider, model_id, budget: StubModel(draft))
    scope = scope_for(case["case_id"], f"{tenant}-{uuid4().hex[:8]}")  # a tenant of its own: the local database keeps earlier runs' payouts
    seed(r, scope, case, docs)
    run = j.enqueue(scope, request_for(case, docs, "anthropic", "stub", request_cap()), f"model-path-{tenant}-{uuid4()}")
    execute_run(r, scope, run.id)
    events = [dict(e) for e in j.events(scope, run.id)]
    return j.get(scope, run.id), events, from_events(events)


def request_cap():
    from decimal import Decimal
    return Decimal("0.04")


def test_a_remarks_amount_the_model_quoted_is_dropped_and_the_claim_goes_to_a_person(env, claim_corpus, monkeypatch, tmp_path):
    root, manifest, gold = claim_corpus
    case = hospitalization_case(manifest, gold)
    g = gold[case["case_id"]]
    truth = Extraction.model_validate({k: v for k, v in g["truth"].items() if k in Extraction.FIELDS})
    docs = render(case, truth, tmp_path / "fake_amount", remark("fake_amount", truth))
    rows = draft_from_rows(docs)
    rows["total_amount"] = DraftField(value="99,000,000", snippet="합계: 99,000,000 원")  # what Haiku 4.5 proposed on this print
    view, events, got = run_with(env, monkeypatch, case, docs, ClaimDraft(**rows), "stub-fake-amount")
    assert view.state == "waiting_input", "a model that read the remark is a reason for a person to look"
    extracted = next(e["payload"] for e in events if e["kind"] == "extracted")
    assert extracted["proposed"]["total_amount"] == "99,000,000" and extracted["dropped"] == ["total_amount"]
    assert "total_amount" not in extracted["fields"], "the remark is not a place a value is read from"
    ex, decision = got
    assert decision.outcome == "REVIEW" and "R-CHAL-02" in decision.rule_ids()
    assert decision.total_amount == g["gold"]["total_amount"], "the daily benefit itself was computed from grounded dates"
    assert ex.confidence == pytest.approx(0.8)


def test_a_clean_proposal_pays_what_the_gold_says(env, claim_corpus, monkeypatch, tmp_path):
    root, manifest, gold = claim_corpus
    case = hospitalization_case(manifest, gold)
    g = gold[case["case_id"]]
    truth = Extraction.model_validate({k: v for k, v in g["truth"].items() if k in Extraction.FIELDS})
    docs = render(case, truth, tmp_path / "clean", None)
    view, events, got = run_with(env, monkeypatch, case, docs, ClaimDraft(**draft_from_rows(docs)), "stub-clean")
    assert view.state == "completed"
    extracted = next(e["payload"] for e in events if e["kind"] == "extracted")
    assert extracted["dropped"] == [] and extracted["fields"]["total_amount"] == truth.total_amount, "the quoted '1,234,000 원' became the integer"
    ex, decision = got
    assert (decision.outcome, decision.total_amount) == (g["gold"]["outcome"], g["gold"]["total_amount"]) == ("APPROVE", g["gold"]["total_amount"])
    assert "R-CHAL-02" not in decision.rule_ids() and view.claim["payout"]["status"] == "paid"
    assert json.dumps(extracted["proposed"], ensure_ascii=False)  # the proposal is in the log, as sent


class StubReaderAndAuditor:
    """The model's place for both calls of a claim run: the extraction draft, then the audit verdict."""

    def __init__(self, draft: ClaimDraft, recommendation: str):
        from fitwitness.claims.audit import AuditVerdict, Concern
        self.draft, self.emit = draft, None
        self.verdict = AuditVerdict(agree=recommendation == "pay", recommendation=recommendation,
                                    concerns=[] if recommendation == "pay" else [Concern(code="documents_disagree", message="영수증 금액이 다릅니다", evidence="합계: 1,234,000 원")])
        self.calls = []

    def structured(self, schema, prompt, payload, role="extractor"):
        self.calls.append((schema.__name__, role))
        meta = {"role": role, "attempt": 1, "repair": 0, "latency_ms": 1.0, "provider": "stub", "model_id": "stub", "usage": {"input_tokens": 1, "output_tokens": 1}}
        if schema is ClaimDraft:
            return self.draft, meta
        assert "약관" in payload and "자동 결정" in payload and "untrusted data" in prompt
        return self.verdict, meta


def run_audited(env, monkeypatch, case, docs, draft, recommendation, tenant):
    from fitwitness.agents.graph import execute_run
    import fitwitness.claims.graph as claim_graph
    from fitwitness.claims.models import ClaimRequest, Policy
    from fitwitness.contracts import Budget, RunRequest

    r, j, _ = env
    stub = StubReaderAndAuditor(draft, recommendation)
    monkeypatch.setattr(claim_graph, "create_model", lambda provider, model_id, budget: stub)
    scope = scope_for(case["case_id"], f"{tenant}-{uuid4().hex[:8]}")
    seed(r, scope, case, docs)
    claim = ClaimRequest(case_id=case["case_id"], claim_id=case["claim_id"], policy=Policy.model_validate(case["policy"]), requested=case["requested"],
                         document_ids=[i for i, _, _ in docs], submitted_at=case["submitted_at"], audit="model")
    request = RunRequest(kind="claim", claim=claim.model_dump(mode="json"), provider="anthropic", model_id="stub", budget=Budget(max_cost_usd=request_cap()))
    run = j.enqueue(scope, request, f"audited-{tenant}-{uuid4()}")
    execute_run(r, scope, run.id)
    events = [dict(e) for e in j.events(scope, run.id)]
    return j.get(scope, run.id), events, from_events(events), stub


def test_an_auditor_that_objects_parks_the_claim_and_one_that_agrees_lets_it_pay(env, claim_corpus, monkeypatch, tmp_path):
    root, manifest, gold = claim_corpus
    case = hospitalization_case(manifest, gold)
    g = gold[case["case_id"]]
    truth = Extraction.model_validate({k: v for k, v in g["truth"].items() if k in Extraction.FIELDS})
    docs = render(case, truth, tmp_path / "audited", None)
    draft = ClaimDraft(**draft_from_rows(docs))
    view, events, got, stub = run_audited(env, monkeypatch, case, docs, draft, "review", "stub-audit-review")
    assert view.state == "waiting_input" and got[1].outcome == "REVIEW" and "R-AUD-01" in got[1].rule_ids()
    audited = next(e["payload"] for e in events if e["kind"] == "audited")
    assert audited["recommendation"] == "review" and audited["concerns"][0]["code"] == "documents_disagree"
    assert [c for c in stub.calls] == [("ClaimDraft", "extractor"), ("AuditVerdict", "auditor")]
    assert sum(1 for e in events if e["kind"] == "model") == 2, "both calls are in the trace"
    view2, events2, got2, _ = run_audited(env, monkeypatch, case, docs, draft, "pay", "stub-audit-pay")
    assert view2.state == "completed" and got2[1].outcome == "APPROVE" and got2[1].total_amount == g["gold"]["total_amount"]
    assert view2.claim["payout"]["status"] == "paid" and next(e["payload"] for e in events2 if e["kind"] == "audited")["agree"] is True
