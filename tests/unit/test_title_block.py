"""Title-block reading: the pairing rules on hand-made words (no Tesseract), and the whole path on a drawn sheet."""
import hashlib
import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from fitwitness.claims import ocr
from fitwitness.contracts import Candidate, DrawingRevision, Requirement
from fitwitness.ingest import title_block as tb
from fitwitness.ingest.pdf import extract_pdf
from fitwitness.verification.conditions import verify

H = 60  # glyph height of the hand-made sheet
SIZE = (4000, 3000)


def w(text, x, y=2500, conf=90.0, width=None, height=H):
    return ocr.Token(text, x, x + (width if width is not None else 40 * len(text)), y, y + height, conf, (1, 1, 1))


def read(words):
    return tb.read_words(tb._merge(words), SIZE)


def test_a_value_is_the_cell_right_next_to_its_label():
    got = read([w("材料", 2000, width=100), w("HT200", 2130, width=200), w("比例", 2000, y=2420, width=100), w("1:1", 2130, y=2420, width=100)])
    assert {(r.field, r.value) for r in got} == {("material", "HT200"), ("scale", "1:1")}
    assert all(0.6 <= r.conf <= 1.0 and 0 <= r.bbox[0] < r.bbox[2] <= 1 for r in got)


def test_an_empty_cell_is_not_filled_with_the_next_thing_on_the_row():
    # the material cell is blank; the drawing number sits further right and must not become the material
    got = read([w("材料", 2000, width=100), w("WLJSQ475-00", 2700, width=420)])
    assert [(r.field, r.value) for r in got] == [("drawing_number", "WLJSQ475-00")]


def test_a_material_grade_is_not_taken_for_a_drawing_number_and_a_page_footer_is_not_either():
    got = read([w("HT200", 2200, width=200), w("Page-271", 2600, y=2900, width=300), w("DJZ-02", 2900, width=240)])
    assert [(r.field, r.value) for r in got] == [("drawing_number", "DJZ-02")]


def test_part_numbers_in_the_parts_list_above_the_header_are_ignored():
    got = read([w("WLJSQ475-001", 2200, y=2200, width=480), w("序号", 1900, y=2300, width=100), w("代号", 2100, y=2300, width=100),
                w("WLJSQ475-00", 2700, y=2600, width=440)])
    assert [r.value for r in got if r.field == "drawing_number"] == ["WLJSQ475-00"]


def test_a_border_read_as_a_tall_token_is_not_a_value():
    got = read([w("材料", 2000, width=100), w("1120", 2120, width=200, height=int(H * 1.9)), w("HT200", 2160, width=200)])
    assert [(r.field, r.value) for r in got if r.field == "material"] == [("material", "HT200")]


def test_a_reading_the_scanner_was_not_sure_of_is_not_reported():
    assert read([w("材料", 2000, width=100, conf=80.0), w("40Cr", 2130, width=160, conf=45.0)]) == []


def test_two_confident_readings_that_disagree_are_dropped_and_a_clear_winner_is_kept():
    tie = read([w("材料", 2000, width=100), w("HT200", 2130, width=200), w("材料", 2000, y=2500, width=100), w("HT250", 2130, y=2504, width=200)])
    assert not [r for r in tie if r.field == "material"]
    clear = [tb.Reading("material", "HT200", 0.93, (0, 0, 1, 1), "材料"), tb.Reading("material", "HT250", 0.70, (0, 0, 1, 1), "材料")]
    assert [r.value for r in tb._one_per_field(clear)] == ["HT200"]


needs_tesseract = pytest.mark.skipif(tb.languages() is None, reason="tesseract is not installed")


def _sheet() -> bytes:
    """An image-only PDF of a drawing sheet whose bottom-right corner holds a title block (English labels)."""
    image = Image.new("RGB", (3000, 2100), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=52)
    draw.rectangle((200, 200, 2800, 1500), outline="black", width=6)  # the part, roughly
    x0, y0 = 1500, 1650
    for row in range(3):
        draw.rectangle((x0, y0 + row * 120, 2800, y0 + (row + 1) * 120), outline="black", width=5)
    draw.line((x0 + 420, y0, x0 + 420, y0 + 360), fill="black", width=5)
    draw.text((x0 + 30, y0 + 30), "MATERIAL", fill="black", font=font)
    draw.text((x0 + 460, y0 + 30), "SUS304", fill="black", font=font)
    draw.text((x0 + 30, y0 + 150), "SCALE", fill="black", font=font)
    draw.text((x0 + 460, y0 + 150), "1:2", fill="black", font=font)
    draw.text((x0 + 30, y0 + 270), "DWG NO", fill="black", font=font)
    draw.text((x0 + 460, y0 + 270), "BR-120", fill="black", font=font)
    buf = io.BytesIO()
    image.save(buf, format="PDF", resolution=150)
    return buf.getvalue()


@needs_tesseract
def test_an_image_only_sheet_yields_observations_the_verifier_will_not_rely_on():
    data = _sheet()
    rev = DrawingRevision(tenant_id="t", document_id="d", drawing_number="X", family_id="f", revision_label="A", source_hash=hashlib.sha256(data).hexdigest())
    facts = extract_pdf(data, rev)
    assert facts, "nothing was read from the title block"
    assert {f.method for f in facts} == {"title_block_ocr"} and {f.certainty for f in facts} == {"uncertain"}
    assert {f.field for f in facts} <= {"material", "drawing_number"}
    assert all(f.source.page == 1 and f.source.bbox for f in facts)
    asked = [Requirement(field="material", value="SUS304"), Requirement(field="drawing_number", value="BR-120")]
    verdicts = [e.verdict.value for e in verify(asked, Candidate(revision_id=rev.id, facts=facts), "snap").evidence]
    assert verdicts == ["unknown", "unknown"]  # an observation shows the reviewer where to look; it never establishes a match


@needs_tesseract
def test_a_blank_page_yields_nothing_and_does_not_fail_the_upload():
    buf = io.BytesIO()
    Image.new("RGB", (1200, 900), "white").save(buf, format="PDF", resolution=150)
    data = buf.getvalue()
    rev = DrawingRevision(tenant_id="t", document_id="d", drawing_number="X", family_id="f", revision_label="A", source_hash=hashlib.sha256(data).hexdigest())
    assert extract_pdf(data, rev) == []


def test_the_reader_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("FITWITNESS_TITLE_BLOCK_OCR", "off")
    data = _sheet()
    rev = DrawingRevision(tenant_id="t", document_id="d", drawing_number="X", family_id="f", revision_label="A", source_hash=hashlib.sha256(data).hexdigest())
    assert extract_pdf(data, rev) == []


def test_a_dotted_code_is_a_drawing_number_only_when_read_surely_and_an_o_among_its_digits_is_a_zero():
    assert [(r.field, r.value) for r in read([w("LJTO1.06", 2700, y=2800, width=320, conf=75.0)])] == [("drawing_number", "LJT01.06")]
    assert read([w("LIT04.20", 2700, y=2800, width=320, conf=68.0)]) == [], "a thin italic code read below the dotted floor may have a wrong letter"
    assert read([w("Ra1.6", 2700, width=200), w("SR12.5", 2700, y=2700, width=240)]) == [], "surface roughness and a radius are not drawing numbers"


def test_a_cell_read_once_with_its_dot_and_once_without_as_two_different_codes_is_not_reported():
    assert read([w("LJT0204", 2700, y=2800, width=300, conf=80.0), w("LIT02.04", 2705, y=2802, width=320, conf=61.0)]) == []


def test_a_grade_in_the_unlabelled_cell_of_a_title_block_is_the_material_and_only_there():
    block = [w("设计", 1000, y=2500, width=100), w("制图", 1000, y=2570, width=100), w("审核", 1000, y=2640, width=100)]
    grade = w("HT150", 2200, y=2400, width=200)
    material = lambda words: [(r.field, r.value) for r in read(words) if r.field == "material"]  # noqa: E731
    assert material(block + [grade]) == [("material", "HT150")]
    assert material([grade]) == [], "without the block's own words the grade could be anything"
    assert material(block + [grade, w("HT200", 2200, y=2300, width=200)]) == [], "two grades: the page does not say which is the material"
    assert material(block + [w("序号", 1900, y=2300, width=100), w("代号", 2100, y=2300, width=100), w("HT150", 2200, y=2600, width=200)]) == [], "an assembly lists many materials and has none"
    assert material(block + [grade, w("材料", 1800, y=2200, width=100)]) == [], "a material label on the page means the grade may belong to a parts list"
