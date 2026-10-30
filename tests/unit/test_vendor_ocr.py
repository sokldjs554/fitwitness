"""The vendor comparison scores both readers with the same field rules (``scripts/eval-vendor-ocr.py``)."""

import importlib.util
from pathlib import Path


def load():
    spec = importlib.util.spec_from_file_location("eval_vendor_ocr", Path("scripts/eval-vendor-ocr.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fields_are_compared_the_way_the_reader_reads_them():
    m = load()
    truth = {
        "insured_name": "김민준",
        "diagnosis_code": "K35.8",
        "diagnosis_date": "2025-12-07",
        "total_amount": 3884730,
        "hospital": "푸른내과의원",
    }
    assert m.same("insured_name", "김 민준", truth["insured_name"])
    assert m.same("diagnosis_code", "k35.8", truth["diagnosis_code"])
    assert m.same("diagnosis_date", "2025.12.07", truth["diagnosis_date"]) and not m.same(
        "diagnosis_date", "2025-12-08", truth["diagnosis_date"]
    )
    assert m.same("total_amount", "3,884,730원", truth["total_amount"])
    assert not m.same("hospital", None, truth["hospital"])
    counter = {}
    m.score(
        ["insured_name", "diagnosis_code", "diagnosis_date"],
        {"insured_name": "김민준", "diagnosis_code": "", "diagnosis_date": "2025-12-08"},
        truth,
        counter,
    )
    assert counter["insured_name"]["correct"] == 1 and counter["diagnosis_code"]["missing"] == 1 and counter["diagnosis_date"]["wrong"] == 1
    summary = m.summarise(counter)
    assert summary == {
        "fields": 3,
        "correct": 1,
        "missing": 1,
        "wrong": 1,
        "accuracy": 0.3333,
        "by_field": {"diagnosis_code": {"missing": 1}, "diagnosis_date": {"wrong": 1}, "insured_name": {"correct": 1}},
    }
    assert m.schema("receipt")["json_schema"]["schema"]["properties"]["total_amount"]["type"] == "integer"
