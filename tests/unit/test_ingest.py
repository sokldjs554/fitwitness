import importlib.util, hashlib
from pathlib import Path
from uuid import uuid4
import pytest
from fitwitness.contracts import DrawingRevision, Interval

ROOT = Path("var/corpus/input/FW-F000-V0")


def funcs():
    assert importlib.util.find_spec("fitwitness.ingest.pdf"), (
        "source extraction missing"
    )
    from fitwitness.ingest.pdf import extract_pdf
    from fitwitness.ingest.cad import extract_step

    return extract_pdf, extract_step


def revision(data):
    return DrawingRevision(
        tenant_id=str(uuid4()),
        document_id=str(uuid4()),
        drawing_number="FW-000-0",
        family_id="f",
        revision_label="A",
        source_hash=hashlib.sha256(data).hexdigest(),
    )


def test_pdf_extracts_real_dimensions_with_regions():
    pdf, _ = funcs()
    data = (ROOT / "drawing.pdf").read_bytes()
    r = revision(data)
    facts = pdf(data, r)
    d = {f.field: f for f in facts}
    assert d["hole_spacing"].value == Interval(low="40", high="40")
    assert d["material"].value == "SUS304"
    assert d["hole_spacing"].source.page == 1
    assert d["hole_spacing"].source.bbox[1] > 0.65
    assert all(f.source.source_hash == hashlib.sha256(data).hexdigest() for f in facts)


def test_invalid_pdf_is_rejected():
    pdf, _ = funcs()
    with pytest.raises(ValueError):
        pdf(b"not a PDF", revision(b"not a PDF"))


def test_hash_mismatch_is_rejected():
    pdf, _ = funcs()
    with pytest.raises(ValueError):
        pdf((ROOT / "drawing.pdf").read_bytes(), revision(b"other"))


def test_step_geometry_is_measured_from_original_bytes():
    _, cad = funcs()
    data = (ROOT / "model.step").read_bytes()
    features = cad(data)
    assert features["bbox_mm"] == pytest.approx([65, 30, 4])
    assert features["volume_mm3"] == pytest.approx(
        65 * 30 * 4 - 2 * 3.141592653589793 * 9 * 4, rel=1e-5
    )
    assert features["source_hash"] == hashlib.sha256(data).hexdigest()
    assert len(features["mesh"]["faces"]) > 10


def test_invalid_step_is_rejected():
    _, cad = funcs()
    with pytest.raises(ValueError):
        cad(b"fake")
