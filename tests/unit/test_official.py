"""The grid reader on the statutory diagnosis certificate and fee statement, redrawn from the forms' labels and lines."""
import io
import json

import pdfplumber
import pytest
from reportlab.pdfgen import canvas

from fitwitness.claims import official, official_fill, vocab
from fitwitness.claims.evaluate import evaluate, on_official_forms
from fitwitness.claims.extract import extract_documents, read_pairs
from fitwitness.claims.official_fill import Slot, fill
from fitwitness.claims.synth import generate_cases

NAME = vocab.SURNAMES[0] + vocab.GIVEN[0]
DIAGNOSIS = dict(insured_name=NAME, diagnosis_name=vocab.DIAGNOSES[0][1], diagnosis_code=vocab.DIAGNOSES[0][0], diagnosis_date="2026-03-10",
                 admission_date="2026-03-10", discharge_date="2026-03-14", hospital=vocab.HOSPITALS[0])
RECEIPT = dict(insured_name=NAME, total_amount=1234567, hospital=vocab.HOSPITALS[1])


def pairs(data: bytes) -> dict[str, str]:
    return {label: text for label, text, _ in read_pairs(data, "d")}


def page_of(data: bytes):
    return pdfplumber.open(io.BytesIO(data)).pages[0]


@pytest.mark.parametrize("kind", ["diagnosis", "receipt"])
def test_a_blank_form_is_recognised_and_gives_nothing_to_read(kind):
    page = page_of(fill(kind, {}, decoys=False))
    assert official.read_page(page, 1, "d") == []  # a form with nothing written on it; not None, which would mean "not a form"
    assert extract_documents([("d", kind, fill(kind, {}, decoys=False))]).evidence == {}


def test_a_page_with_label_colon_value_lines_is_not_an_official_form(tmp_path):
    cases = generate_cases(tmp_path, n=3, seed=2)
    pdf = next(tmp_path.glob("CLM-*/*.pdf")).read_bytes()
    assert official.read_page(page_of(pdf), 1, "d") is None
    assert cases


@pytest.mark.parametrize("jitter", [0.0, 1.5, 4.5])
def test_the_diagnosis_values_are_read_from_their_cells_wherever_the_print_lands(jitter):
    for seed in range(8):
        got = pairs(fill("diagnosis", DIAGNOSIS, seed=str(seed), jitter=jitter))
        assert got == {"환자성명:": NAME, "병명:": DIAGNOSIS["diagnosis_name"], "질병분류기호:": DIAGNOSIS["diagnosis_code"], "진단일:": "2026-03-10",
                       "입원일:": "2026-03-10", "퇴원일:": "2026-03-14", "발행기관:": DIAGNOSIS["hospital"]}


@pytest.mark.parametrize("jitter", [0.0, 3.0])
def test_the_fee_statement_total_and_patient_are_read(jitter):
    got = pairs(fill("receipt", RECEIPT, seed="1", jitter=jitter))
    assert got == {"환자성명:": NAME, "합계:": "1,234,567", "발행기관:": RECEIPT["hospital"]}


def test_the_cells_beside_the_ones_that_are_read_are_left_alone():
    clean = pairs(fill("diagnosis", DIAGNOSIS, seed="3", decoys=False))
    crowded = pairs(fill("diagnosis", DIAGNOSIS, seed="3", decoys=True))  # onset date, secondary disease and phone number filled in
    assert crowded == clean
    receipt = pairs(fill("receipt", RECEIPT, seed="3", decoys=True))  # registration number and department
    assert receipt == pairs(fill("receipt", RECEIPT, seed="3", decoys=False))


def test_a_field_the_clerk_left_empty_is_not_invented():
    partial = {k: v for k, v in DIAGNOSIS.items() if k not in ("discharge_date", "hospital")}
    got = pairs(fill("diagnosis", partial, seed="4"))
    assert "퇴원일:" not in got and "발행기관:" not in got and got["입원일:"] == "2026-03-10"
    ex = extract_documents([("d", "diagnosis", fill("diagnosis", partial, seed="4"))])
    assert ex.discharge_date is None and ex.hospital is None and ex.diagnosis_code == DIAGNOSIS["diagnosis_code"]


def test_a_value_written_right_against_its_label_is_still_its_own_word(monkeypatch):
    monkeypatch.setitem(official_fill.DIAGNOSIS, "hospital", Slot(195.5, 548))  # the label ends at 195.3
    data = fill("diagnosis", DIAGNOSIS, seed=None)
    assert any(w["text"].startswith("명칭:") and len(w["text"]) > 3 for w in page_of(data).extract_words())  # the PDF has it as one word
    assert pairs(data)["발행기관:"] == DIAGNOSIS["hospital"]


def test_a_value_written_over_the_spaces_a_form_prints_stays_whole():
    buffer = io.BytesIO()
    c = official_fill._canvas(buffer)
    c.setFont(official_fill.FONT_NAME, 10)
    c.drawString(60, 700, "년" + " " * 20 + "월")  # how the printed form leaves room
    c.drawString(75, 700, "2026")  # written over the spaces
    c.save()
    words = [w["text"] for w in official._words(page_of(buffer.getvalue()))]
    assert "2026" in "".join(words) and not any(len(w) == 1 and w.isdigit() for w in words)


def test_evidence_points_inside_the_page_at_the_value():
    for label, value, ref in official.read_page(page_of(fill("diagnosis", DIAGNOSIS, seed="5")), 1, "d"):
        x0, top, x1, bottom = ref.bbox
        assert 0 <= x0 < x1 <= 1 and 0 <= top < bottom <= 1 and ref.snippet == f"{label} {value}"


def test_claims_over_the_official_forms_are_read_exactly_and_routed_like_the_text_documents(tmp_path):
    generate_cases(tmp_path, n=22, seed=3)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    gold = json.loads((tmp_path / "gold.json").read_text())
    forms = {"diagnosis": None, "receipt": None}
    for case in manifest["cases"]:
        if case["scenario"] in ("ocr_noise", "date_conflict"):
            continue
        docs = [(d["id"], d["kind"], (tmp_path / case["case_id"] / f"{d['id']}.pdf").read_bytes()) for d in case["documents"]]
        truth = gold[case["case_id"]]["truth"]
        ex = extract_documents(on_official_forms(docs, truth, forms))
        for f in gold[case["case_id"]]["visible_fields"]:
            assert getattr(ex, f) == truth[f], (case["case_id"], f)
        assert ex.confidence == 1.0 and not ex.scanned
    summary = evaluate(tmp_path, forms=forms)["summary"]
    assert summary["wrong_pay_count"] == 0 and summary["wrong_deny_count"] == 0 and summary["forms"] == {"diagnosis": "redrawn", "receipt": "redrawn"}
