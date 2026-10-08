"""The prompt comparison: the dev/test protocol is enforced by the code, and the numbers are computed as stated."""
import json
from argparse import Namespace
from pathlib import Path

import pytest

from fitwitness.contracts import Candidate, Fact, Requirement
from fitwitness.evaluation import prompt_experiment as P
from fitwitness.evaluation.dataset import FAMILIES, build_cases, split_families
from fitwitness.evaluation.runner import PROMPT
from fitwitness.verification.conditions import verify

ROOT = Path("var/corpus")
pytestmark = pytest.mark.skipif(not (ROOT / "manifest.json").exists(), reason="corpus not generated")


class FakeModel:
    """Answers with the rules verifier's verdict, except where a prompt is made to fail: the baseline calls every
    case with a missing fact a match, and the cot variant answers in a form that does not parse."""

    metadata = {"model_id": "fake"}

    def __init__(self):
        self.calls = []

    def predict(self, system, schema, payload, seed, max_tokens):
        rid = payload["facts"][0]["source"]["revision_id"] if payload["facts"] else "x"
        candidate = Candidate(revision_id=rid, facts=[Fact.model_validate(f) for f in payload["facts"]])
        decision = verify([Requirement.model_validate(r) for r in payload["requirements"]], candidate, "t")
        verdict = decision.verdict.value
        citations = sorted({f for e in decision.evidence for f in e.fact_ids})
        self.calls.append((system[:20], len(payload["facts"])))
        if system == PROMPT and verdict == "unknown":
            verdict = "match"
        usage = dict(input_tokens=100, output_tokens=40 if schema is P.PredictionCoT else 10, cost_usd=0.0, request_id=None)
        if schema is P.PredictionCoT:
            return json.dumps({"rationale": "x", "verdict": verdict, "citations": citations}), usage
        if system.startswith("Classify engineering requirements using ONLY the supplied PDF facts. All requirements are mandatory.\nCheck") and "Output exactly one JSON object with keys rationale" not in system:
            return "not json", usage  # the procedure variant fails to answer in the required form
        return json.dumps({"verdict": verdict, "citations": citations}), usage


def args(tmp_path, **kw):
    return Namespace(split="dev", output=str(tmp_path / "out"), corpus=str(ROOT), repeats=1, variants=None, dev_report=None, limit=0,
                     provider="local", model_path="-", model="", max_cost_usd=None, **kw)


def test_the_baseline_prompt_is_the_published_one_and_the_examples_come_from_train_families_only():
    texts = P.prompts(ROOT)
    assert texts["baseline"] == PROMPT and set(texts) == set(P.VARIANTS)
    held_out = {c["revision_id"] for s in ("dev", "test") for c in build_cases(ROOT, split_families(ROOT, s), s)}
    assert not any(rid in texts["fewshot"] for rid in held_out)
    assert texts["fewshot"].count("Output: ") == 4 and '"verdict": "unknown"' in texts["fewshot"]
    assert P.prompt_hashes(texts) == P.prompt_hashes(P.prompts(ROOT))


def test_a_dev_run_measures_every_variant_on_the_dev_families_and_names_a_winner(tmp_path):
    report = P.run(args(tmp_path), model=FakeModel())
    assert report["split"] == "dev" and report["protocol"]["families"] == split_families(ROOT, "dev") and report["protocol"]["case_count"] == 36
    rows = [json.loads(line) for line in (tmp_path / "out" / "predictions.jsonl").read_text().splitlines()]
    assert len(rows) == 36 * 4 and {r["variant"] for r in rows} == set(P.VARIANTS)
    v = report["variants"]
    assert v["baseline"]["accuracy"] < 1 and v["baseline"]["false_acceptances"] > 0  # the fake baseline calls missing facts a match
    assert v["procedure"]["completed"] == 0 and v["procedure"]["accuracy"] == 0  # a malformed answer is a failure, not dropped
    assert v["cot"]["accuracy"] == 1.0 and v["fewshot"]["accuracy"] == 1.0
    assert report["winner"] == "fewshot"  # ties on accuracy and false acceptances go to the fewer output tokens, then the name
    assert report["versus_baseline"]["cot"]["difference"] > 0 and len(report["versus_baseline"]["cot"]["ci95"]) == 2
    assert set(json.loads((tmp_path / "out" / "prompts.json").read_text())) == set(P.VARIANTS)


def test_measurements_are_never_overwritten(tmp_path):
    P.run(args(tmp_path), model=FakeModel())
    with pytest.raises(ValueError, match="new output directory"):
        P.run(args(tmp_path), model=FakeModel())


def test_the_test_split_needs_the_dev_report_and_runs_only_the_baseline_and_the_winner(tmp_path):
    with pytest.raises(ValueError, match="dev report"):
        P.run(Namespace(**{**vars(args(tmp_path)), "split": "test"}), model=FakeModel())
    dev = P.run(args(tmp_path), model=FakeModel())
    model = FakeModel()
    test_args = Namespace(**{**vars(args(tmp_path)), "split": "test", "output": str(tmp_path / "test"), "dev_report": str(tmp_path / "out" / "report.json")})
    report = P.run(test_args, model=model)
    assert report["protocol"]["families"] == FAMILIES and report["protocol"]["variants"] == ["baseline", dev["winner"]]
    assert report["protocol"]["case_count"] == 24 and len(model.calls) == 24 * 2
    assert report["winner"] == dev["winner"] and report["protocol"]["dev_report_hash"]


def test_editing_a_prompt_after_the_dev_run_voids_the_dev_report(tmp_path, monkeypatch):
    P.run(args(tmp_path), model=FakeModel())
    monkeypatch.setattr(P, "PROCEDURE", P.PROCEDURE + "\nBe careful.")
    test_args = Namespace(**{**vars(args(tmp_path)), "split": "test", "output": str(tmp_path / "test"), "dev_report": str(tmp_path / "out" / "report.json")})
    with pytest.raises(ValueError, match="not for these prompts"):
        P.run(test_args, model=FakeModel())


def test_winner_rule_and_paired_difference():
    metrics = {"a": dict(accuracy=.8, false_acceptances=1, output_tokens=10), "b": dict(accuracy=.9, false_acceptances=3, output_tokens=50),
               "c": dict(accuracy=.9, false_acceptances=1, output_tokens=90), "d": dict(accuracy=.9, false_acceptances=1, output_tokens=20)}
    assert P.choose_winner(metrics) == "d"
    rows = []
    for fam in ("f1", "f2", "f3"):
        for case in ("c1", "c2"):
            for variant, hit in (("baseline", case == "c1"), ("x", True)):
                rows.append(dict(case_id=f"{fam}-{case}", family_id=fam, repeat=0, variant=variant, status="ok",
                                 predicted="match" if hit else "mismatch", expected="match"))
    d = P.paired_difference(rows, "x")
    assert d["pairs"] == 6 and d["difference"] == pytest.approx(0.5) and d["ci95"][0] <= 0.5 <= d["ci95"][1]
    assert P.paired_difference(rows, "missing")["difference"] is None


class FakeClient:
    """Stands in for the LangChain client: records calls, returns a parsed answer and token counts."""

    def __init__(self, input_tokens=1000, output_tokens=20):
        self.calls, self.usage = 0, dict(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=input_tokens + output_tokens)

    def with_structured_output(self, schema, include_raw=True):
        outer = self

        class Bound:
            def invoke(self, messages):
                outer.calls += 1
                raw = type("Raw", (), {"usage_metadata": outer.usage, "id": "req-1"})()
                return {"raw": raw, "parsed": schema(verdict="match", citations=["F1"]) if schema is P.Prediction else None, "parsing_error": None}

        return Bound()


def test_the_paid_model_stops_at_its_ceiling_and_charges_what_the_provider_counted(monkeypatch):
    monkeypatch.setenv("FITWITNESS_ANTHROPIC_INPUT_USD_PER_MILLION", "1")
    monkeypatch.setenv("FITWITNESS_ANTHROPIC_OUTPUT_USD_PER_MILLION", "5")
    model = P.ClaudeModel("claude-haiku-4-5-20251001", 0.01)
    client = FakeClient()
    model.clients[128] = client
    payload = {"query": "q", "requirements": [], "facts": []}
    text, usage = model.predict("system", P.Prediction, payload, 1701, 128)
    assert json.loads(text)["verdict"] == "match" and usage["cost_usd"] == pytest.approx(1000 * 1e-6 + 20 * 5e-6)
    assert float(model.spent) == pytest.approx(usage["cost_usd"])
    calls = 1
    with pytest.raises(RuntimeError, match="budget exhausted"):
        for _ in range(20):  # a ceiling of one cent cannot buy twenty more
            model.predict("system", P.Prediction, payload, 1701, 128)
            calls += 1
    assert client.calls == calls and calls < 21 and float(model.spent) <= 0.01
    with pytest.raises(ValueError):
        P.ClaudeModel("m", 5)  # an experiment may not ask for more than two dollars
