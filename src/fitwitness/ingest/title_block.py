"""Read the title block of an image-only drawing (a scan, a photo, a PDF made of one picture).

``ingest/pdf.py`` reads vector annotations: a page whose text is a picture yields nothing there. A
mechanical drawing keeps its identity in the title block in the bottom-right corner (drawing number,
material, scale), so this module renders the page, runs Tesseract over a few overlapping crops of
that corner in sparse-text mode, pairs each known label with the cell to its right, and finds the
drawing number by its shape.

Whatever it reads is an observation, not a fact the verifier may rely on. ``extract_pdf`` stores it
with certainty "uncertain": the verifier then answers "insufficient evidence" and shows the reviewer
where on the page the text was read, instead of declaring a match on a misreading. The labels are
the ones used on Chinese (GB), Korean and English drawings; the Chinese reading needs the
``chi_sim`` language data (``apt install tesseract-ocr-chi-sim``), the Korean one ``kor``.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

from PIL import Image

from fitwitness.claims import ocr

# Where the title block can be: the bottom-right of the sheet. Overlapping crops, because blocks differ
# in height (a parts list stacked above the block makes it tall) and a crop that is too big or too
# small makes Tesseract's page segmentation lose the table cells.
CROPS = ((0.40, 0.70), (0.40, 0.78), (0.40, 0.85))  # (left, top) as a fraction of the sheet; right and bottom are the edge
SCALES = (1.0, 0.7, 0.5)
MAX_SIDE = 6000  # pixels: a scan is rendered no larger than this on its long side
MIN_CONF = 40.0  # tokens below this are page noise
MIN_READING = 0.60  # a value read less surely than this is not reported at all
LANGS = ("chi_sim", "kor", "eng")

LABELS = {
    "材料": "material", "材质": "material", "材料名称": "material", "재질": "material", "소재": "material",
    "MATERIAL": "material", "MATL": "material", "MAT": "material",
    "比例": "scale", "축척": "scale", "SCALE": "scale",
    "图号": "drawing_number", "图样代号": "drawing_number", "图纸编号": "drawing_number", "도번": "drawing_number", "도면번호": "drawing_number",
    "DWGNO": "drawing_number", "DRAWINGNO": "drawing_number", "PARTNO": "drawing_number",
}
# Labels that are not fields we read but end a value ("the cell to the right" must not be one of these).
OTHER_LABELS = {"数量", "重量", "质量", "单位", "制图", "审核", "设计", "批准", "工艺", "名称", "代号", "序号", "备注", "单件", "总计", "标记", "处数", "分区",
                "수량", "중량", "단위", "설계", "검도", "승인", "QTY", "WEIGHT", "UNIT", "NAME", "DRAWN", "CHECKED", "APPROVED", "TITLE"}
# Prefixes that make a hyphenated code something else: a page footer ("Page-271"), a figure, a standard.
NOT_A_NUMBER = ("PAGE", "FIG", "FIGURE", "TABLE", "TAB", "GB", "GBT", "ISO", "DIN", "JIS", "ANSI", "ASME", "ASTM", "IEC", "EN")
SEPARATORS = set("|丨/\\:：;-_.,'\"`~!l")
# A drawing number: a hyphenated code ("DJZ-02", "QF02-10", "WLJSQ475-00") or three or more letters and digits ("ZPT19").
# Two letters and digits ("HT200", "ZL107", "LY12", "Q235") are material designations, not numbers.
NUMBER = re.compile(r"^(?:[A-Z]{1,6}\d{0,4}(?:-\d{1,4}){1,3}|[A-Z]{3,6}\d{1,4})$")
BARS = "|丨 \t"


@dataclass
class Reading:
    field: str  # material, scale, drawing_number
    value: str
    conf: float  # 0..1, the weakest token it was read from
    bbox: tuple[float, float, float, float]  # page-normalised
    label: str | None  # the label it sat next to; None for a drawing number found by its shape


def available() -> bool:
    return ocr.available()


def languages() -> str | None:
    """The recognition languages that are installed, joined for Tesseract, or None without the binary."""
    exe = ocr.binary()
    if not exe:
        return None
    if override := os.getenv("FITWITNESS_DRAWING_OCR_LANG"):
        return override
    try:
        listed = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=20).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return None
    chosen = [lang for lang in LANGS if lang in listed]
    return "+".join(chosen) if chosen else None


def render(pdf: bytes, page_index: int):
    import pypdfium2 as pdfium

    page = pdfium.PdfDocument(pdf)[page_index]
    width, height = page.get_size()
    scale = min(200 / 72, MAX_SIDE / max(width, height))
    return page.render(scale=scale).to_pil().convert("L")


def _norm(text: str) -> str:
    return re.sub(r"[\s:：()（）\[\]]+", "", text).upper()


def _tokens(image, lang: str) -> list[ocr.Token]:
    """Tokens of every crop at every scale, in page pixels, one per place: where readings overlap the more confident one stays.

    The same label is read at one scale and lost at another (a label's glyphs are about 70 px tall on a
    large scan, taller than Tesseract likes), so the page is read at full size and shrunk."""
    width, height = image.size
    best: dict[tuple, ocr.Token] = {}
    for left, top in CROPS:
        x0, y0 = int(width * left), int(height * top)
        crop = image.crop((x0, y0, width, height))
        for scale in SCALES:
            shown = crop if scale == 1.0 else crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))), Image.LANCZOS)
            # sparse text finds boxed cells on a real scan; block mode reads a clean, evenly laid-out sheet better
            for t in [t for psm in ((11, 6) if scale == 1.0 else (11,)) for t in ocr.recognise(shown, lang=lang, timeout=120.0, psm=psm)]:
                if t.conf < MIN_CONF:
                    continue
                t = ocr.Token(t.text, t.x0 / scale + x0, t.x1 / scale + x0, t.top / scale + y0, t.bottom / scale + y0, t.conf, t.line)
                key = (round((t.x0 + t.x1) / 2 / 25), round((t.top + t.bottom) / 2 / 25))
                if key not in best or t.conf > best[key].conf:
                    best[key] = t
    return sorted(best.values(), key=lambda t: (round(t.top / 20), t.x0))


def _cjk(text: str) -> bool:
    return bool(text) and all("\u3400" <= c <= "\u9fff" or "\uac00" <= c <= "\ud7a3" for c in text)


def _merge(tokens: list[ocr.Token]) -> list[ocr.Token]:
    """Drop the cell borders Tesseract reads as text, and join the glyphs of a CJK label that were read apart ("材" "料")."""
    clean = []
    for t in tokens:
        text = t.text.strip(BARS)
        if text:
            clean.append(ocr.Token(text, t.x0, t.x1, t.top, t.bottom, t.conf, t.line))
    out: list[ocr.Token] = []
    for t in sorted(clean, key=lambda t: (round((t.top + t.bottom) / 2 / 30), t.x0)):
        last = out[-1] if out else None
        h = min(t.bottom - t.top, last.bottom - last.top) if last else 0
        if last and _cjk(last.text) and _cjk(t.text) and len(last.text) + len(t.text) <= 4 \
                and abs((last.top + last.bottom) - (t.top + t.bottom)) < 0.7 * h and 0 <= t.x0 - last.x1 < 0.6 * h:
            out[-1] = ocr.Token(last.text + t.text, last.x0, t.x1, min(last.top, t.top), max(last.bottom, t.bottom), min(last.conf, t.conf), last.line)
        else:
            out.append(t)
    return out


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", text).strip(BARS + ":：;,.")


def _is_label(text: str) -> bool:
    n = _norm(text)
    return n in LABELS or n in OTHER_LABELS


def _value_right_of(label: ocr.Token, words: list[ocr.Token], taken: set[int]) -> ocr.Token | None:
    """The cell to the right of a label on the same row; None when the cell is empty or holds something else.

    A token much taller than the label is a cell border read together with its neighbours, a token that
    looks like a drawing number belongs to the number's cell, and a token another field already used is taken."""
    h = label.bottom - label.top
    row = [w for w in words if w is not label and id(w) not in taken and abs((w.top + w.bottom) / 2 - (label.top + label.bottom) / 2) < 0.7 * h
           and 0 <= w.x0 - label.x1 < 1.6 * h and (w.bottom - w.top) <= 1.6 * h]
    for w in sorted(row, key=lambda w: w.x0):
        text = _clean(w.text)
        if not text or all(c in SEPARATORS for c in text):
            continue
        return None if _is_label(text) or _number(text) else w
    return None


SCALE = re.compile(r"^\d{1,2}[:：]\d{1,3}$")
# A material designation: a grade with a digit in it ("HT200", "40Cr", "SUS304", "45"), or a plain CJK name ("橡胶").
MATERIAL = re.compile(r"^(?=.*\d)[A-Za-z0-9][A-Za-z0-9.\-/]{0,11}$|^[\u3400-\u9fff\uac00-\ud7a3]{2,6}$")


def _plausible(field: str, text: str) -> bool:
    if field == "material":
        return bool(MATERIAL.match(text))
    if field == "scale":
        return bool(SCALE.match(text))
    return True


def _cell_value(image, label: ocr.Token, lang: str, field: str) -> ocr.Token | None:
    """Read the cell to the right of a label by itself, one line, enlarged.

    The page-wide pass often reads a label and loses the small value beside it (a "40Cr" inside a boxed
    cell). The label token can also come back as wide as label and value together, so the cell is taken to
    start two glyphs after the label's left edge."""
    h = label.bottom - label.top
    start = label.x0 + 2.1 * h if label.x1 - label.x0 > 2.6 * h else label.x1
    box = (int(start), max(0, int(label.top - 0.2 * h)), int(min(image.width, start + 5 * h)), min(image.height, int(label.bottom + 0.2 * h)))
    if box[2] - box[0] < 20 or box[3] - box[1] < 10:
        return None
    cell = image.crop(box)
    factor = 2 if h < 90 else 1
    if factor != 1:
        cell = cell.resize((cell.width * factor, cell.height * factor))
    padded = Image.new("L", (cell.width + 40, cell.height + 40), 255)
    padded.paste(cell, (20, 20))
    for t in sorted(ocr.recognise(padded, lang=lang, timeout=60.0, psm=7), key=lambda t: t.x0):
        text = _clean(t.text)
        if not text or all(c in SEPARATORS for c in text) or _is_label(text) or _number(text):
            continue
        if field == "scale" and not SCALE.match(text):
            continue
        return ocr.Token(text, box[0] + (t.x0 - 20) / factor, box[0] + (t.x1 - 20) / factor, box[1] + (t.top - 20) / factor, box[1] + (t.bottom - 20) / factor, t.conf, t.line)
    return None


def _number(text: str) -> bool:
    t = _clean(text).upper().replace("\u2014", "-").replace("\uff0d", "-")
    prefix = re.match(r"[A-Z]+", t)
    return bool(NUMBER.match(t)) and any(c.isdigit() for c in t) and len(t) >= 4 and not (prefix and prefix.group(0) in NOT_A_NUMBER)


def read_image(image, lang: str | None = None) -> list[Reading]:
    lang = lang or languages()
    if not lang:
        return []
    words = _merge(_tokens(image, lang))
    return read_words(words, image.size, lambda label, field: _cell_value(image, label, lang, field))


def read_words(words: list[ocr.Token], size: tuple[int, int], cell=None) -> list[Reading]:
    """Turn the words of a title-block corner into readings. ``cell(label, field)`` re-reads one value cell when the page-wide pass lost it."""
    width, height = size
    readings: list[Reading] = []
    taken: set[int] = set()
    box = lambda ts: (min(t.x0 for t in ts) / width, min(t.top for t in ts) / height, max(t.x1 for t in ts) / width, max(t.bottom for t in ts) / height)  # noqa: E731
    # The drawing number has no label on a Chinese (GB) block: find it by its shape, below the parts-list header if there is one.
    header = [w for w in words if _norm(w.text) in ("代号", "序号")]
    floor = max((w.bottom for w in header), default=0)
    found = [w for w in words if w.top >= floor and _number(w.text)]
    found = [w for w in found if w.conf >= MIN_READING * 100]
    if found:
        best = max(found, key=lambda w: (w.conf, w.top))
        taken.add(id(best))
        readings.append(Reading("drawing_number", _clean(best.text).upper().replace("\u2014", "-").replace("\uff0d", "-"), best.conf / 100.0, box([best]), None))
    for label in words:
        field = LABELS.get(_norm(label.text))
        if not field or field == "drawing_number" and any(r.field == "drawing_number" for r in readings):
            continue
        value = _value_right_of(label, words, taken) or (cell(label, field) if cell else None)
        if value is None:
            continue
        text = _clean(value.text)
        conf = min(label.conf, value.conf) / 100.0
        if not text or len(text) > 16 or conf < MIN_READING or not _plausible(field, text):
            continue
        taken.add(id(value))
        readings.append(Reading(field, text, conf, box([label, value]), label.text))
    return _one_per_field(readings)


def _one_per_field(readings: list[Reading]) -> list[Reading]:
    """The same label is often read at several scales: keep the most confident reading of each field. Two
    different values read about as surely as each other mean the page does not say which, so neither is kept."""
    out: list[Reading] = []
    for field in dict.fromkeys(r.field for r in readings):
        ranked = sorted((r for r in readings if r.field == field), key=lambda r: -r.conf)
        rival = next((r for r in ranked[1:] if r.value.upper() != ranked[0].value.upper()), None)
        if rival is not None and ranked[0].conf - rival.conf < 0.10:
            continue
        out.append(ranked[0])
    return out


def read_page(pdf: bytes, page_index: int) -> list[Reading]:
    if not available():
        return []
    return read_image(render(pdf, page_index))
