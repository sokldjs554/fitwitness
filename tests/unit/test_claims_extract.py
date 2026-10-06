"""Reading synthetic claim PDFs back: values, source positions and format tolerance."""
import json
import pytest
from fitwitness.claims.extract import extract_documents, ground, normalise_amount, normalise_date
from fitwitness.claims.models import Extraction
from fitwitness.claims.synth import generate_cases


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("claims")
    generate_cases(root, n=22, seed=3)
    return root, json.loads((root / "manifest.json").read_text()), json.loads((root / "gold.json").read_text())


def docs_of(root, case):
    return [(d["id"], d["kind"], (root / case["case_id"] / f"{d['id']}.pdf").read_bytes()) for d in case["documents"]]


def test_dates_and_amounts_in_every_printed_format_normalise():
    assert normalise_date("2025-06-01") == normalise_date("2025.6.1") == normalise_date("2025/06/01") == normalise_date("2025년 6월 1일") == "2025-06-01"
    assert normalise_date("2O25-06-01") is None and normalise_date("2025-13-01") is None
    assert normalise_amount("1,234,000원") == normalise_amount("금 1,234,000원정") == normalise_amount("1234000원") == 1234000


def test_visible_truth_fields_are_read_exactly_except_under_noise(corpus):
    root, manifest, gold = corpus
    misses = []
    for case in manifest["cases"]:
        if case["scenario"] in ("ocr_noise", "date_conflict"):
            continue
        ex = extract_documents(docs_of(root, case))
        truth = gold[case["case_id"]]["truth"]
        for f in gold[case["case_id"]]["visible_fields"]:
            if getattr(ex, f) != truth[f]:
                misses.append((case["case_id"], case["scenario"], f, getattr(ex, f), truth[f]))
        assert ex.confidence == 1.0, case["case_id"]
        for f in gold[case["case_id"]]["visible_fields"]:
            assert f in ex.evidence and ex.evidence[f].snippet
    assert misses == []


def test_noise_lowers_confidence_and_keeps_unparseable_text_for_the_validator(corpus):
    root, manifest, gold = corpus
    noisy = [c for c in manifest["cases"] if c["scenario"] == "ocr_noise"]
    assert noisy
    for case in noisy:
        ex = extract_documents(docs_of(root, case))
        bad = [f for f in ("admission_date", "discharge_date", "diagnosis_date", "surgery_date") if getattr(ex, f) and len(getattr(ex, f)) != 10 or (getattr(ex, f) and not getattr(ex, f)[:4].isdigit())]
        assert ex.confidence < 1.0 or not bad


def test_grounding_keeps_only_values_whose_snippet_is_in_the_document(corpus):
    root, manifest, gold = corpus
    case = next(c for c in manifest["cases"] if c["scenario"] == "clean")
    docs = docs_of(root, case)
    truth = gold[case["case_id"]]["truth"]
    draft = {
        "admission_date": {"value": truth["admission_date"], "snippet": f"입원일: {truth['admission_date']}"},
        "discharge_date": {"value": "2031-01-01", "snippet": "퇴원일: 2031-01-01"},  # invented: not printed anywhere
        "total_amount": {"value": truth["total_amount"], "snippet": f"합계: {truth['total_amount']:,}원"},
    }
    ex = ground(draft, docs)
    assert ex.admission_date == truth["admission_date"] and ex.total_amount == truth["total_amount"]
    assert ex.discharge_date is None and "discharge_date" not in ex.evidence
    assert ex.confidence == pytest.approx(0.8)
    assert set(ex.evidence) == {"admission_date", "total_amount"}
