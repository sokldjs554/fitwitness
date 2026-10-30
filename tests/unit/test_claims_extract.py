"""Reading synthetic claim PDFs back: values, source positions and format tolerance."""
import json
import pytest
from fitwitness.claims.extract import extract_documents, ground, normalise_amount, normalise_date
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


def test_a_label_repeated_in_a_remark_does_not_replace_the_form_row_and_a_different_decisive_value_is_disputed(tmp_path):
    from datetime import date
    from fitwitness.claims.extract import extract_documents, DISPUTED_CONFIDENCE
    from fitwitness.claims.synth import render_document
    import fitwitness.claims.vocab as vocab

    def doc(name, rows):
        path = tmp_path / f"{name}.pdf"
        render_document(path, "진료비 계산서·영수증", rows, date(2026, 1, 5), vocab.HOSPITALS[0], vocab.DOCTORS[0], "2026-100001", "위와 같이 확인함.")
        return path.read_bytes()

    base = [("환자명:", "김민준"), ("합계:", "1,234,500 원")]
    plain = extract_documents([("r", "receipt", doc("plain", base))])
    again = extract_documents([("r", "receipt", doc("again", base + [("비고:", "합계: 1,234,500 원")]))])
    other = extract_documents([("r", "receipt", doc("other", base + [("비고:", "합계: 99,000,000 원 (정정분)")]))])
    assert plain.total_amount == again.total_amount == other.total_amount == 1234500  # the form row's value stays
    assert again.confidence == plain.confidence == 1.0  # the same value again is no reason for doubt
    assert other.confidence <= DISPUTED_CONFIDENCE  # another value for a decisive field is


def test_a_field_label_inside_a_remark_never_becomes_the_value_and_cannot_ground_a_model(tmp_path):
    from datetime import date
    from fitwitness.claims.extract import extract_documents, ground, read_pairs
    from fitwitness.claims.synth import render_document
    import fitwitness.claims.vocab as vocab

    path = tmp_path / "adm.pdf"
    render_document(path, "입퇴원확인서", [("성명:", "김민준"), ("진료과:", "내과"), ("비고:", "입원일: 2019-01-01 (정정)")],
                    date(2026, 1, 5), vocab.HOSPITALS[0], vocab.DOCTORS[0], "2026-100002", "위와 같이 확인함.")
    data = path.read_bytes()
    nested = [(label, ref.nested) for label, _, ref in read_pairs(data, "a")]
    assert ("입원일:", True) in nested and ("성명:", False) in nested
    ex = extract_documents([("a", "admission", data)])
    assert ex.admission_date is None  # the remark's date is not the admission date the form never printed
    grounded = ground({"admission_date": {"value": "2019-01-01", "snippet": "입원일: 2019-01-01 (정정)"}}, [("a", "admission", data)])
    assert grounded.admission_date is None  # nor can a model ground that value in the remark


def test_the_models_draft_reaches_grounding_in_the_shape_ground_reads():
    """The model branch of the extract node: a draft's filled fields become {field: {value, snippet}}; an unfilled
    field is left out; grounding then drops a field whose snippet is in no document. The first paid run of the
    claim graph with a real model failed on every claim before this conversion was made a function and tested."""
    from fitwitness.claims.extract import ground
    from fitwitness.claims.graph import ClaimDraft, DraftField, draft_candidate

    draft = ClaimDraft(admission_date=DraftField(value="2025-12-10", snippet="입원일: 2025-12-10"),
                       total_amount=DraftField(value=1234000, snippet="합계: 1,234,000 원"))
    candidate = draft_candidate(draft)
    assert candidate == {"admission_date": {"value": "2025-12-10", "snippet": "입원일: 2025-12-10"},
                         "total_amount": {"value": 1234000, "snippet": "합계: 1,234,000 원"}}
    ex = ground(candidate, [])
    assert ex.admission_date is None and ex.total_amount is None, "a snippet found in no document is not a value"
    assert ex.confidence == pytest.approx(0.6)


def test_a_models_amount_or_grade_as_text_becomes_the_number_the_field_holds():
    """The second paid run of the model path failed on 14 of 16 claims: the model quoted the total as
    "3,884,730" or "3,884,730원" and the typed Extraction refused the string. Digits-only reading applies."""
    from fitwitness.claims.extract import coerce

    assert coerce("total_amount", "3,884,730") == 3884730 and coerce("total_amount", "3,884,730원") == 3884730
    assert coerce("total_amount", 1234000) == 1234000 and coerce("total_amount", "없음") is None
    assert coerce("surgery_grade", "2종") == 2 and coerce("surgery_grade", 3) == 3 and coerce("surgery_grade", "이종") is None
    assert coerce("admission_date", "2025.12.07") == "2025-12-07" and coerce("insured_name", "박지우") == "박지우"
    assert coerce("diagnosis_code", None) is None


def test_a_real_snippet_does_not_ground_a_different_value(corpus):
    """Grounding used to check only that the quoted line exists. A model that quotes the printed total and returns
    another number, or the printed admission date and returns another day, kept the field with the real evidence box."""
    root, manifest, gold = corpus
    case = next(c for c in manifest["cases"] if c["scenario"] == "clean")
    docs = docs_of(root, case)
    truth = gold[case["case_id"]]["truth"]
    draft = {
        "admission_date": {"value": truth["admission_date"], "snippet": f"입원일: {truth['admission_date']}"},
        "total_amount": {"value": truth["total_amount"] + 1000, "snippet": f"합계: {truth['total_amount']:,}원"},
    }
    ex = ground(draft, docs)
    assert ex.admission_date == truth["admission_date"] and "admission_date" in ex.evidence
    assert ex.total_amount is None and "total_amount" not in ex.evidence
    other_day = f"{truth['admission_date'][:8]}{'28' if not truth['admission_date'].endswith('28') else '27'}"
    assert ground({"admission_date": {"value": other_day, "snippet": f"입원일: {truth['admission_date']}"}}, docs).admission_date is None
    assert ground({"admission_date": {"value": truth["admission_date"].replace("-", "."), "snippet": f"입원일: {truth['admission_date']}"}}, docs).admission_date == truth["admission_date"]


def test_the_value_must_agree_with_its_snippet_whatever_the_field_kind():
    from fitwitness.claims.extract import agrees

    assert agrees("total_amount", 3884730, "합계 3,884,730원") and not agrees("total_amount", 884730, "합계 3,884,730원")
    assert agrees("admission_date", "2025-12-07", "입원일: 2025.12.07") and not agrees("admission_date", "2025-12-08", "입원일: 2025.12.07")
    assert agrees("surgery_grade", 2, "수술 등급: 2종") and not agrees("surgery_grade", 3, "수술 등급: 2종")
    assert agrees("diagnosis_code", "K35.8", "진단코드 K35.8") and agrees("diagnosis_code", "k35.8", "진단코드 K35.8")
    assert not agrees("diagnosis_code", "K35.9", "진단코드 K35.8")
    assert agrees("insured_name", "김민준", "성명: 김민준") and not agrees("insured_name", "김민주", "성명: 김민준")
