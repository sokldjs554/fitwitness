"""A claim that arrives as a picture: simulated scans of the synthetic documents, read with Tesseract.

Skipped where Tesseract and its Korean data are not installed (CI installs them)."""

import json
import subprocess

import pytest

from fitwitness.claims import ocr
from fitwitness.claims.evaluate import evaluate, run_pipeline
from fitwitness.claims.scan import scan_pdf
from fitwitness.claims.synth import generate_cases


def _has_korean() -> bool:
    exe = ocr.binary()
    if not exe:
        return False
    out = subprocess.run([exe, "--list-langs"], capture_output=True, text=True).stdout
    return "kor" in out.split()


needs_ocr = pytest.mark.skipif(not _has_korean(), reason="tesseract with Korean data is not installed")


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("scans")
    generate_cases(root, n=22, seed=11)
    return root, json.loads((root / "manifest.json").read_text())


def _of(manifest, scenario):
    return next(c["case_id"] for c in manifest["cases"] if c["scenario"] == scenario)


@needs_ocr
def test_a_clean_scan_of_a_normal_claim_is_read_and_paid_the_same_as_the_text_version(corpus):
    root, manifest = corpus
    cid = _of(manifest, "clean")
    scanned = evaluate(root, scan="clean", cases=[cid])
    text = evaluate(root, cases=[cid])
    assert scanned["summary"]["wrong_pay_count"] == 0
    assert scanned["rows"][0]["predicted"] in ("APPROVE", "REVIEW")
    assert scanned["summary"]["field_accuracy"] >= 0.8
    if scanned["rows"][0]["predicted"] == "APPROVE":
        assert scanned["rows"][0]["amount"] == text["rows"][0]["amount"]


@needs_ocr
def test_a_badly_degraded_scan_never_ends_in_a_wrong_payment(corpus):
    root, manifest = corpus
    cases = [_of(manifest, s) for s in ("clean", "high_amount", "exclusion", "duplicate")]
    result = evaluate(root, scan="heavy", cases=cases, workers=2)
    assert result["summary"]["wrong_pay_count"] == 0  # unsure means a person looks at it, never a payment on a guess


@needs_ocr
def test_a_scan_carries_the_readers_confidence_into_the_extraction(corpus):
    root, manifest = corpus
    case = next(c for c in manifest["cases"] if c["case_id"] == _of(manifest, "clean"))
    ex, _, _ = run_pipeline(root, case, scan="medium")
    assert 0.0 < ex.confidence < 1.0  # a text layer is certain (1.0); a scan never is
    assert ex.evidence  # the boxes the values were read from


def test_without_tesseract_a_scan_yields_nothing_and_the_claim_goes_to_a_person(corpus, monkeypatch):
    root, manifest = corpus
    case = next(c for c in manifest["cases"] if c["case_id"] == _of(manifest, "clean"))
    monkeypatch.setenv("FITWITNESS_TESSERACT", "/nonexistent/tesseract")
    assert not ocr.available()
    ex, decision, _ = run_pipeline(root, case, scan="clean")
    assert decision.outcome == "REVIEW" and not ex.evidence


@needs_ocr
def test_a_model_extraction_grounded_on_a_scan_carries_the_scanned_flag_so_a_denial_goes_to_a_person(corpus):
    """The scan run of the model path denied a payable claim on a code OCR read as 005.1: on the rules reader a
    denial resting on a scan goes to a person (R-SCAN-01); grounding did not mark the extraction as scanned."""
    from fitwitness.claims.extract import extract_documents, ground
    from fitwitness.claims.models import Policy
    from fitwitness.claims.policy import PRODUCTS, adjudicate

    root, manifest = corpus
    case = next(
        c for c in manifest["cases"] if c["policy"]["product_id"] == "HLTH-A" and "surgery" in c["requested"] and c["scenario"] == "clean"
    )
    text = [(d["id"], d["kind"], (root / case["case_id"] / f"{d['id']}.pdf").read_bytes()) for d in case["documents"]]
    scans = [(i, k, scan_pdf(data, "clean", i)) for i, k, data in text]
    policy, product = Policy.model_validate(case["policy"]), PRODUCTS["HLTH-A"]
    outcomes = {}
    for label, docs in (("text", text), ("scan", scans)):
        read = extract_documents(docs)  # the rules reader's values and quotes, as a model would quote them
        draft = {
            f: {"value": "Z41.1" if f == "diagnosis_code" else getattr(read, f), "snippet": read.evidence[f].snippet} for f in read.evidence
        }
        ex = ground(draft, docs)
        assert ex.diagnosis_code is None, "a wrong value behind a real quote is not grounded: the quoted line does not show it"
        assert ex.scanned is (label == "scan")
        # What the claim graph then meets is a code that a reading did produce and the exclusion table refuses.
        ex = ex.model_copy(update={"diagnosis_code": "Z41.1"})
        outcomes[label] = adjudicate(policy, product, ["surgery"], ex, {k for _, k, _ in docs}, set(), [])
    assert outcomes["text"].outcome == "DENY", "an excluded code on a text layer is a denial the rules make on their own"
    assert outcomes["scan"].outcome == "REVIEW" and any(r.rule_id == "R-SCAN-01" for r in outcomes["scan"].reasons)
