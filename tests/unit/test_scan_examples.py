import importlib.util
import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from fitwitness.api.app import create_app

ROOT = Path(__file__).resolve().parents[2]
HOW = {"second_read", "second_read_confirmed", "repaired", "sum_checked"}


def _record():
    return TestClient(create_app()).get("/api/evaluations/scan-examples").json()


def test_every_scan_example_is_served_with_its_own_page_picture():
    # Static evidence: no session, no database, no OCR engine. The picture is what the boxes are drawn on.
    client = TestClient(create_app())
    record = _record()
    assert record["status"] == "measured" and record["examples"]
    for ex in record["examples"]:
        page = client.get("/api/evaluations/scan-examples/" + ex["page"]["image"].split("/")[-1])
        assert page.status_code == 200 and page.headers["content-type"] == "image/jpeg"
        assert Image.open(io.BytesIO(page.content)).size == (ex["page"]["width"], ex["page"]["height"])
    for name in ("scan-examples.json", "a.png", "..jpg", "UPPER.jpg", "missing-page.jpg"):
        assert client.get("/api/evaluations/scan-examples/" + name).status_code == 404, name


def test_a_shown_example_never_pays_on_a_value_it_got_wrong_or_doubted():
    # A regenerated record that put a wrong payment on the desk would be worse than none: check the record itself.
    for ex in _record()["examples"]:
        decisive = [f for f in ex["fields"] if f["decisive"]]
        for f in ex["fields"]:
            assert set(f["how"]) <= HOW, f
            if f.get("bbox"):
                x0, y0, x1, y1 = f["bbox"]
                assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1, f
            assert f["flagged"] or not f["decisive"] or f["read"] is not None, f  # a decisive value nobody read is a reason for a person
        if ex["outcome"] == "APPROVE":
            assert ex["gold"] == "APPROVE" and ex["amount"] == ex["gold_amount"], ex["id"]
            assert all(f["correct"] and not f["flagged"] for f in decisive), ex["id"]
        if any(f["decisive"] and not f["correct"] for f in ex["fields"]):
            assert ex["outcome"] != "APPROVE", ex["id"]


def test_the_picker_chooses_by_its_written_rules_and_skips_a_wrong_payment():
    spec = importlib.util.spec_from_file_location("build_scan_examples", ROOT / "scripts/build-scan-examples.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def field(name, correct=True, how=(), flagged=False):
        return {"field": name, "decisive": True, "printed": True, "correct": correct, "how": list(how), "flagged": flagged, "doc_id": "d1"}

    def claim(case_id, fields, outcome="APPROVE", gold="APPROVE", rules=()):
        return {
            "case_id": case_id,
            "fields": fields,
            "outcome": outcome,
            "gold": gold,
            "amount": 100,
            "gold_amount": 100,
            "rules": list(rules),
            "documents": [{"id": "d1", "kind": "diagnosis"}],
        }

    wrong_payment = claim("CLM-1", [field("diagnosis_code", correct=False)])  # approved though a decisive value is wrong
    clean = claim("CLM-2", [field("diagnosis_code"), field("total_amount")])
    rescued = claim("CLM-3", [field("diagnosis_code", how=["second_read"])])
    picked = {p["id"]: p["claim"]["case_id"] for p in mod.pick([wrong_payment, clean, rescued])}
    assert picked == {"plain": "CLM-2", "second-read": "CLM-3"}
