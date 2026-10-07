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
