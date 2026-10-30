"""The scanned-form reader: rules from pixels, registration of the form's grid, and the cell reads (``claims/official_scan.py``)."""

import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image, ImageDraw

from fitwitness.claims import ocr, official_scan as S
from fitwitness.claims.extract import extract_documents
from fitwitness.claims.official_fill import LAYOUTS, fill
from fitwitness.claims.scan import scan_pdf

PX = 200 / 72  # pixels per point


def grid_page(kind: str, *, scale: float = 1.0, dx: float = 0.0, dy: float = 0.0, angle: float = 0.0, noise: float = 0.0) -> Image.Image:
    """The form's ruling lines alone, drawn as a scanner would see them: thin, offset, scaled, tilted, with noise."""
    image = Image.new("L", (1654, 2339), 255)
    pen = ImageDraw.Draw(image)
    layout = LAYOUTS[kind]
    for y, x0, x1, width in layout["h"]:
        pen.line(
            [((x0 * scale + dx) * PX, (y * scale + dy) * PX), ((x1 * scale + dx) * PX, (y * scale + dy) * PX)],
            fill=40,
            width=max(2, round(width * PX)),
        )
    for x, y0, y1 in layout["v"]:
        pen.line([((x * scale + dx) * PX, (y0 * scale + dy) * PX), ((x * scale + dx) * PX, (y1 * scale + dy) * PX)], fill=40, width=3)
    if angle:
        image = image.rotate(angle, resample=Image.BICUBIC, fillcolor=255)
    if noise:
        arr = np.asarray(image, dtype=np.float32) + np.random.default_rng(3).normal(0, noise, (image.height, image.width))
        image = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    return image


def rules_of(image: Image.Image) -> S.Rules:
    return S.find_rules(S.straighten(image))


@pytest.mark.parametrize("kind", ["diagnosis", "receipt"])
def test_the_forms_own_grid_is_registered_on_a_clean_page(kind):
    fit = S.register(rules_of(grid_page(kind)), kind)
    assert fit is not None and fit.share > 0.8  # the form's shortest lines (under 18 points) are never found as rules
    assert abs(fit.sx - 1) < 0.005 and abs(fit.sy - 1) < 0.005 and abs(fit.tx) < 1 and abs(fit.ty) < 1


@pytest.mark.parametrize("kind", ["diagnosis", "receipt"])
def test_a_form_printed_larger_shifted_and_scanned_crooked_is_registered_at_its_real_place(kind):
    fit = S.register(rules_of(grid_page(kind, scale=1.03, dx=9, dy=-6, angle=1.2, noise=12)), kind)
    assert fit is not None and fit.share > 0.75
    assert abs(fit.sx - 1.03) < 0.006 and abs(fit.sy - 1.03) < 0.006
    # the point the form calls (45.3, 134.5) lies where it was drawn: (45.3 * 1.03 + 9, 134.5 * 1.03 - 6)
    layout = LAYOUTS[kind]
    x, y = layout["v"][0][0], layout["h"][0][0]
    assert abs(fit.x(x) - (x * 1.03 + 9)) < 2.0 and abs(fit.y(y) - (y * 1.03 - 6)) < 2.0


def test_the_other_forms_grid_is_not_taken_for_this_one():
    receipt = rules_of(grid_page("receipt"))
    assert S.register(receipt, "diagnosis") is None


def test_a_page_of_text_has_no_ruling_lines():
    image = Image.new("L", (1654, 2339), 255)
    pen = ImageDraw.Draw(image)
    for row in range(40):  # 40 lines of "text": short strokes, no rule longer than a letter
        for col in range(60):
            pen.rectangle([100 + col * 22, 200 + row * 40, 103 + col * 22, 225 + row * 40], fill=0)  # a vertical stroke 25 px tall
            pen.rectangle([100 + col * 22, 212 + row * 40, 112 + col * 22, 214 + row * 40], fill=0)  # a short horizontal one
    rules = S.find_rules(image)
    assert len(rules.horizontal) < S.MIN_RULES and not rules.vertical


def test_a_scanned_page_without_the_forms_lines_is_not_a_form(monkeypatch):
    monkeypatch.setattr(ocr, "binary", lambda: "/bin/true")  # the reader must refuse before it reads anything
    assert S.read_image(Image.new("L", (1654, 2339), 255), 1, "d") is None


def word(text: str) -> dict:
    return {"text": text, "x0": 0.0, "x1": 1.0, "top": 0.0, "bottom": 1.0, "conf": 90.0}


@pytest.mark.parametrize(
    "read, expected",
    [
        ([word("김지호"), word("|")], "김지호"),
        ([word(".강현우")], ".강현우".strip(".")),
        ([word("장현우"), word("Ee"), word("Sass")], "장현우"),
        ([word("급성"), word("충수염")], "급성 충수염"),
        ([word("BaF")], None),
        ([word("두:."), word(".")], None),  # one syllable and a smudge is not a name
        ([word("MBASHHSI")], None),
    ],
)
def test_a_cell_that_is_not_mostly_hangul_is_not_a_name(read, expected):
    assert S._name(read) == expected


needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None or "kor" not in subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout,
    reason="tesseract with Korean data is not installed",
)

CASE = dict(
    insured_name="박지우",
    diagnosis_name="급성 충수염",
    diagnosis_code="K35.8",
    diagnosis_date="2025-12-07",
    admission_date="2025-12-07",
    discharge_date="2025-12-17",
    hospital="푸른내과의원",
    total_amount=3884730,
)


@needs_tesseract
@pytest.mark.parametrize("level", ["clean", "medium"])
def test_a_scanned_diagnosis_certificate_is_read_by_its_cells(level):
    got = extract_documents([("d", "diagnosis", scan_pdf(fill("diagnosis", CASE, seed="x"), level, "d"))])
    assert (got.insured_name, got.diagnosis_code, got.diagnosis_date, got.admission_date, got.discharge_date) == (
        "박지우",
        "K35.8",
        "2025-12-07",
        "2025-12-07",
        "2025-12-17",
    )
    assert got.diagnosis_name.replace(" ", "") == "급성충수염" and got.scanned


@needs_tesseract
def test_a_scanned_fee_statement_gives_its_total_and_patient():
    got = extract_documents([("r", "receipt", scan_pdf(fill("receipt", CASE, seed="x"), "medium", "r"))])
    assert got.total_amount == 3884730 and got.insured_name == "박지우"


@needs_tesseract
@pytest.mark.parametrize("kind", ["diagnosis", "receipt"])
def test_a_scanned_blank_form_gives_nothing_for_a_reader_to_take_for_a_value(kind):
    got = extract_documents([("b", kind, scan_pdf(fill(kind, {}, decoys=False), "medium", "b"))])
    assert got.evidence == {}
