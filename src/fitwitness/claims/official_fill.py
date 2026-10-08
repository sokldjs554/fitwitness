"""Put fake values on the two official forms, where a hospital writes them.

There are no real claim documents in this repository. What exists is the blank statutory form, and the
evaluation needs forms that carry values the generator knows the truth of. So this module writes the
generator's values onto

  * the real blank form, when the caller has downloaded it (the forms are published by the ministry and
    are not part of this repository), or
  * a redraw of the form's printed labels and ruling lines (``official_layouts.json``), which is what the
    tests and CI use. Labels and lines of a statutory form are the law's text, not a creative work.

The positions are measured from the forms themselves: each value goes into the cell its label points to,
dates go to the left of the printed 년/월/일, with the jitter a printer and a clerk add (a few points of
offset, a font size within half a point). The reader (``official.py``) is never told these positions.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

FONT = Path(__file__).with_name("fonts") / "NotoSansKR-forms.ttf"
FONT_NAME = "NotoSansKR-forms"
LAYOUTS = json.loads(Path(__file__).with_name("official_layouts.json").read_text())
PAGE = (595, 841)
DESCENT = 0.11  # of the embedded font, so a word's top edge lands where the layout says


@dataclass(frozen=True)
class Slot:
    x: float
    top: float
    size: float = 10.0
    align: str = "left"


# value slots, in the same page coordinates pdfplumber reports (points from the top left)
DIAGNOSIS = {
    "insured_name": Slot(168, 196),
    "diagnosis_name": Slot(246, 258),
    "diagnosis_code": Slot(412, 274),
    "hospital": Slot(200, 548),
}
DIAGNOSIS_DATES = {  # x of the printed 년, 월, 일 and the top of that line
    "diagnosis_date": ((449.9, 472.4, 495.0), 315.5),
    "admission_date": ((249.7, 274.4, 299.0), 418.5),
    "discharge_date": ((432.2, 457.0, 481.7), 418.5),
}
# what a hospital writes in the cells next to the ones the reader takes: the reader must leave them alone
DIAGNOSIS_DECOYS = {"secondary": Slot(246, 286), "phone": Slot(404, 236)}
ONSET = ((229.8, 256.9, 283.9), 315.5)  # 발병 연월일, the cell beside the one that holds 진단 연월일
RECEIPT_DECOYS = {"registration": Slot(62, 62.4, 9.0), "department": Slot(62, 87.5, 9.0)}
RECEIPT = {
    "insured_name": Slot(160, 62.4, 9.0),
    "total_amount": Slot(530, 118.0, 9.0, "right"),
    "hospital": Slot(318, 546.0, 9.0),
}


def _y(top: float, size: float) -> float:
    return PAGE[1] - top - size * (1 - DESCENT)


def _canvas(buffer: BytesIO):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT)))
    return canvas.Canvas(buffer, pagesize=PAGE, invariant=1)


def _draw_layout(c, kind: str) -> None:
    from reportlab.pdfbase import pdfmetrics

    layout = LAYOUTS[kind]
    for top, x0, x1, width in layout["h"]:
        c.setLineWidth(width)
        c.line(x0, PAGE[1] - top, x1, PAGE[1] - top)
    c.setLineWidth(0.6)
    for x, top, bottom in layout["v"]:
        c.line(x, PAGE[1] - top, x, PAGE[1] - bottom)
    for text, x0, x1, top, bottom in layout["words"]:
        size = max(5.0, bottom - top)
        natural = pdfmetrics.stringWidth(text, FONT_NAME, size)
        t = c.beginText(x0, _y(top, size))
        t.setFont(FONT_NAME, size)
        if natural > 0:
            t.setHorizScale(100.0 * (x1 - x0) / natural)
        t.textOut(text)
        c.drawText(t)


def _put(c, slot: Slot, text: str, rng: random.Random | None, jitter: float) -> None:
    dx = rng.uniform(-jitter, jitter) if rng else 0.0
    dy = rng.uniform(-jitter * 0.6, jitter * 0.6) if rng else 0.0
    size = slot.size + (rng.uniform(-0.5, 0.5) if rng else 0.0)
    c.setFont(FONT_NAME, size)
    draw = c.drawRightString if slot.align == "right" else c.drawString
    draw(slot.x + dx, _y(slot.top + dy, size), text)


def _put_date(c, iso: str, markers: tuple[float, float, float], top: float, rng: random.Random | None, jitter: float) -> None:
    d = date.fromisoformat(iso)
    padded = bool(rng and rng.random() < 0.3)
    parts = (str(d.year), f"{d.month:02d}" if padded else str(d.month), f"{d.day:02d}" if padded else str(d.day))
    for text, marker in zip(parts, markers):
        _put(c, Slot(marker - 2.0, top, 10.0, "right"), text, rng, jitter)


def fill(kind: str, values: dict, *, blank: bytes | None = None, seed: str | None = None, jitter: float = 1.5,
         decoys: bool = True) -> bytes:
    """The form with ``values`` written on it, as a one page PDF with a text layer.

    ``kind`` is "diagnosis" or "receipt". ``blank`` is the real blank form's bytes; without it the
    redrawn labels and lines are used. ``values`` uses the generator's field names (insured_name,
    diagnosis_name, diagnosis_code, diagnosis_date, admission_date, discharge_date, hospital, total_amount);
    a field that is missing or empty is left blank, as a clerk would. ``seed`` makes the jitter reproducible.
    ``decoys`` also fills the neighbouring cells (onset date, secondary disease, phone, registration number,
    department) with plausible values the reader must not take for a field."""
    rng = random.Random(seed) if seed is not None else None
    buffer = BytesIO()
    c = _canvas(buffer)
    if blank is None:
        _draw_layout(c, kind)
    c.setFillColorRGB(0.05, 0.05, 0.25)
    if kind == "diagnosis":
        for field, slot in DIAGNOSIS.items():
            if values.get(field):
                _put(c, slot, str(values[field]), rng, jitter)
        for field, (markers, top) in DIAGNOSIS_DATES.items():
            if values.get(field):
                _put_date(c, values[field], markers, top, rng, jitter)
        if decoys:
            from fitwitness.claims import vocab

            other = next(name for _, name in vocab.DIAGNOSES if name != values.get("diagnosis_name"))
            _put(c, DIAGNOSIS_DECOYS["secondary"], other, rng, jitter)
            _put(c, DIAGNOSIS_DECOYS["phone"], "02-1234-5678", rng, jitter)
            if values.get("diagnosis_date"):
                onset = (date.fromisoformat(values["diagnosis_date"]) - timedelta(days=3)).isoformat()
                _put_date(c, onset, ONSET[0], ONSET[1], rng, jitter)
    elif kind == "receipt":
        if values.get("insured_name"):
            _put(c, RECEIPT["insured_name"], values["insured_name"], rng, jitter)
        if values.get("hospital"):
            _put(c, RECEIPT["hospital"], values["hospital"], rng, jitter)
        if decoys:
            _put(c, RECEIPT_DECOYS["registration"], "00123456", rng, jitter)
            _put(c, RECEIPT_DECOYS["department"], "내과", rng, jitter)
        if values.get("total_amount") is not None:
            _put(c, RECEIPT["total_amount"], f"{int(values['total_amount']):,}", rng, jitter)
    else:
        raise ValueError(kind)
    c.save()
    if blank is None:
        return buffer.getvalue()
    from pypdf import PdfReader, PdfWriter

    base = PdfReader(BytesIO(blank)).pages[0]
    base.merge_page(PdfReader(BytesIO(buffer.getvalue())).pages[0])
    out = PdfWriter()
    out.add_page(base)
    result = BytesIO()
    out.write(result)
    return result.getvalue()
