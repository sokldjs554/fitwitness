"""Read the two official forms from a page picture, the way ``official.py`` reads them from a text layer.

A scanned diagnosis certificate or fee statement has no words to ask for and no ruling lines to ask for
either. What the text-layer reader takes from the PDF has to be recovered from pixels, and a statutory form is
a fixed document, so the way to do it is registration, not page OCR:

1. the page is straightened (the scanner's skew is found from its own ruling lines),
2. the ruling lines are found as long dark runs, and the form is recognised by laying its known grid over
   them (a scale and an offset per axis); a page whose lines do not fit is not one of the forms,
3. the lines are erased, so the cell borders stop being read as letters,
4. each printed label is checked in its own cell (a form that fits by accident is refused when its labels do
   not read), and each value is read from the cell its label points to, cut out at twice the size.

Reading a whole gridded page in one pass loses most of the written values (Tesseract's block mode treats the
rules as text and the cells as columns), and finding the labels by page OCR is erratic: the same label is read
on one scan and lost on a cleaner one. Reading the cell the label points to does not have either problem.
What comes back has the shape of ``ocr.pairs``: label, value, evidence box, confidence. The confidence is the
lowest of the cell's tokens, so a doubtful value is sent to a person by the same rule as any other scan.

The grid and the label positions are the ones in ``official_layouts.json`` (measured from the two forms), and a
form that is printed at another size or offset is registered to them; a different edition of the form, with
other cells, is not read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from fitwitness.claims import ocr
from fitwitness.claims.models import EvidenceRef
from fitwitness.claims.official import HINTS, LAYOUTS, TEMPLATES, Grid, Zone, _box, _find, _make, _n, _region, _rows, _value

PT = 72.0 / ocr.DPI  # points per pixel
CONTRAST = 35  # how much darker than its surroundings a pixel is to count as ink (for the page's tilt)
DARKER, COVER, COVER_WINDOW = 8, 0.7, 31  # a line pixel is at least this much darker than the paper, in this share of a window
FAINT, AVERAGE, BESIDE = 14, 15, 4  # for the rules: how much darker, averaged over this many pixels along the line, and standing out from rows this far off
MIN_H, MIN_V = 60, 36  # shortest ruling lines, in pixels (22 and 13 points): a Hangul stroke is shorter than both
HOLES = 0.9  # a rule may have this share of its length drawn; the scanner leaves gaps in thin lines
MIN_RULES = 8  # a page with fewer long rules is not one of the forms
ZOOM = 2  # a cell is read at this scale
TOLERANCE = 2.0  # points: how far a ruling line may lie from where the registered form puts it
NEAR, FAR_BETTER = 8.0, 0.15  # points a form may lie from its printed place before a fit must explain this much more of the grid
FIT = 0.6  # the share of the form's lines that must be found for the page to be taken for it
SCALES = np.arange(0.94, 1.0601, 0.002)  # a scanner or a copier changes the size of a page by a few percent
LABELS_CHECKED, LABELS_READ = 3, 0.5  # the longest labels of a form are read in their cells; this share of them must read as printed
INSETS = (3, 4, 5)  # pixels cut off every side of a cell before it is read, to keep out what a rule leaves at its edge; a blurrier
# scan leaves more, so a cell that gave nothing usable is read again with the next one
WIPE = 3  # the form's lines are painted out this many pixels wide (wider would cut into text that sits close to its border)
BLANK = 0.001  # a cell with less ink than this (share of its pixels, well below the paper's grey) is empty


def _ink(image: Image.Image) -> np.ndarray:
    """True where a pixel is clearly darker than the neighbourhood it sits in (a page lit unevenly still has rules)."""
    grey = image.convert("L")
    around = np.asarray(grey.filter(ImageFilter.BoxBlur(12)), dtype=np.int16)
    return (around - np.asarray(grey, dtype=np.int16)) > CONTRAST


def _along(image: Image.Image, axis: int) -> np.ndarray:
    """True where a pixel lies on a thin line running along ``axis`` (1: left to right).

    How much darker than its surroundings a pixel is, averaged over AVERAGE pixels along the line: a hairline a blurred,
    noisy scan has almost lost still stands out that way, and the noise does not. Two things keep text out of it. A
    line must stand out from the rows beside it (BESIDE pixels to each side), and it must be drawn without gaps: at
    least COVER of the pixels in a window of COVER_WINDOW are darker than the paper at all, which the tops of a row of
    letters are not."""
    grey = image.convert("L")
    around = np.asarray(grey.filter(ImageFilter.BoxBlur(12)), dtype=np.int16)
    diff = (around - np.asarray(grey, dtype=np.int16)).astype(np.float32)
    if axis == 0:
        diff = diff.T
    width = diff.shape[1]

    def along(values: np.ndarray, run: int) -> np.ndarray:
        c = np.zeros((values.shape[0], width + 1), dtype=np.float32)
        np.cumsum(values, axis=1, out=c[:, 1:])
        lo, hi = np.clip(np.arange(width) - run // 2, 0, width), np.clip(np.arange(width) + run // 2 + 1, 0, width)
        return (c[:, hi] - c[:, lo]) / np.maximum(hi - lo, 1)

    mean = along(diff, AVERAGE)
    beside = np.maximum(np.roll(mean, BESIDE, axis=0), np.roll(mean, -BESIDE, axis=0))
    out = (mean > FAINT) & (mean - beside > FAINT / 2) & (along((diff > DARKER).astype(np.float32), COVER_WINDOW) >= COVER)
    return out if axis == 1 else out.T


def _tilt(ink: np.ndarray) -> float:
    """The angle (degrees, counter-clockwise, in PIL's sense) that makes the page's long horizontal strokes level."""
    small = Image.fromarray((ink * 255).astype(np.uint8)).resize((ink.shape[1] // 2, ink.shape[0] // 2), Image.BILINEAR)

    def sharp(angle: float) -> float:
        rows = (np.asarray(small.rotate(angle, resample=Image.BILINEAR)) > 100).sum(axis=1).astype(np.float64)
        return float((rows ** 2).sum())

    best = max(np.arange(-3.0, 3.01, 0.25), key=sharp)
    return float(max(np.arange(best - 0.25, best + 0.251, 0.05), key=sharp))


def straighten(image: Image.Image) -> Image.Image:
    angle = _tilt(_ink(image))
    return image.rotate(angle, resample=Image.BICUBIC, fillcolor=255) if abs(angle) >= 0.1 else image


def _long(ink: np.ndarray, length: int) -> np.ndarray:
    """Pixels that lie on a horizontal run of ``length`` pixels that is at least HOLES drawn."""
    height, width = ink.shape
    if width < length:
        return np.zeros_like(ink)
    c = np.zeros((height, width + 1), dtype=np.int32)
    np.cumsum(ink, axis=1, out=c[:, 1:])
    hit = (c[:, length:] - c[:, :-length]) >= int(length * HOLES)  # hit[:, s]: the window starting at s is mostly ink
    h = np.zeros((height, hit.shape[1] + 1), dtype=np.int32)
    np.cumsum(hit, axis=1, out=h[:, 1:])
    x = np.arange(width)
    lo, hi = np.clip(x - length + 1, 0, hit.shape[1]), np.clip(x + 1, 0, hit.shape[1])
    return (h[:, hi] - h[:, lo]) > 0  # pixel x is on a rule when some hit window starting in [x-length+1, x] covers it


@dataclass(frozen=True)
class Rules:
    horizontal: list[tuple[float, float, float]]  # (y, x0, x1) in points
    vertical: list[tuple[float, float, float]]  # (x, y0, y1) in points
    mask: np.ndarray  # the pixels the rules cover


def _spans(mask1d: np.ndarray, minimum: int) -> list[tuple[int, int]]:
    edges = np.diff(np.concatenate(([0], mask1d.astype(np.int8), [0])))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    return [(int(s), int(e)) for s, e in zip(starts, ends) if e - s >= minimum]


def _lines(covered: np.ndarray, minimum: int) -> list[tuple[float, float, float]]:
    """(position across, start along, end along) in points, of each rule in a mask whose rules run along axis 1."""
    out = []
    rows = np.flatnonzero(covered.any(axis=1))
    if rows.size == 0:
        return out
    for g in np.split(rows, np.flatnonzero(np.diff(rows) > 3) + 1):
        band = covered[g[0]:g[-1] + 1]
        for s, e in _spans(band.any(axis=0), minimum):
            ys = np.flatnonzero(band[:, s:e].any(axis=1))
            out.append(((g[0] + float(ys.mean())) * PT, s * PT, e * PT))
    return out


def find_rules(image: Image.Image) -> Rules:
    smear = AVERAGE - 1  # averaging along a line makes every stroke that much longer; a rule is that much longer than a letter's stroke
    h, v = _long(_along(image, 1), MIN_H + smear), _long(_along(image, 0).T, MIN_V + smear).T
    return Rules(_lines(h, MIN_H + smear), _lines(v.T, MIN_V + smear), h | v)


# --- registration ------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Fit:
    """page = scale * form + shift, per axis, and how much of the form's grid the page's lines account for."""

    sx: float
    tx: float
    sy: float
    ty: float
    share: float

    def x(self, v: float) -> float:
        return self.sx * v + self.tx

    def y(self, v: float) -> float:
        return self.sy * v + self.ty


def _axis(found: list[float], known: list[float], keep: int = 12) -> list[tuple[int, float, float]]:
    """The best ``keep`` (matched lines, scale, shift) that lay the known lines onto the found ones. A scale gives
    several candidate shifts, not only the best one: where the page has many rules, a shift of one table row also
    matches nearly every line, and the right shift is then a lower peak, not the highest."""
    d, t = np.array(sorted(set(round(v, 1) for v in found))), np.array(sorted(set(round(v, 1) for v in known)))
    if len(d) < 2 or len(t) < 2:
        return []
    out = []
    for s in SCALES:
        diffs = np.sort((d[:, None] - s * t[None, :]).ravel())
        reach = np.searchsorted(diffs, diffs + 2 * TOLERANCE, side="right")
        counts = reach - np.arange(len(diffs))
        order = np.argsort(-counts, kind="stable")
        taken: list[float] = []
        for i in order[:400]:
            if counts[i] < 0.5 * counts[order[0]] or len(taken) >= 5:
                break
            shift = float(diffs[i:reach[i]].mean())
            if any(abs(shift - x) < 3 * TOLERANCE for x in taken):
                continue
            taken.append(shift)
            out.append((int(sum(np.abs(d - m).min() <= TOLERANCE for m in s * t + shift)), float(s), shift))
    out.sort(key=lambda c: (-c[0], abs(c[1] - 1.0), abs(c[2])))
    return out[:keep]


def _refine(found: list[float], known: list[float], s: float, shift: float) -> tuple[float, float]:
    d = np.array(found)
    pairs = [(k, float(d[np.abs(d - (s * k + shift)).argmin()])) for k in known if np.abs(d - (s * k + shift)).min() <= TOLERANCE]
    if len(pairs) < 3 or max(p[0] for p in pairs) - min(p[0] for p in pairs) < 50:
        return s, shift
    a, b = np.polyfit([p[0] for p in pairs], [p[1] for p in pairs], 1)
    return float(a), float(b)


def _covered(fit: Fit, rules: Rules, layout: dict) -> float:
    """The share of the form's lines that a ruling line of the page lies on, in position and in extent."""
    hits = 0
    for y, x0, x1, _ in layout["h"]:
        py, a, b = fit.y(y), fit.x(x0), fit.x(x1)
        hits += any(abs(fy - py) <= TOLERANCE and min(fx1, b) - max(fx0, a) >= 0.4 * min(b - a, fx1 - fx0) for fy, fx0, fx1 in rules.horizontal)
    for x, y0, y1 in layout["v"]:
        px, a, b = fit.x(x), fit.y(y0), fit.y(y1)
        hits += any(abs(fx - px) <= TOLERANCE and min(fy1, b) - max(fy0, a) >= 0.4 * min(b - a, fy1 - fy0) for fx, fy0, fy1 in rules.vertical)
    return hits / (len(layout["h"]) + len(layout["v"]))


def register(rules: Rules, kind: str) -> Fit | None:
    """Where the form ``kind`` lies on the page, or None when its grid is not there.

    The fee statement's table repeats every 12.5 points, so a grid laid one row off explains almost as many lines as
    the right one. A page is scanned with its content within a few points of where it was printed, so a fit that
    keeps the form near its place wins unless a far one explains clearly more."""
    layout = LAYOUTS[kind]
    found_y, found_x = [y for y, _, _ in rules.horizontal], [x for x, _, _ in rules.vertical]
    known_y, known_x = [h[0] for h in layout["h"]], [v[0] for v in layout["v"]]
    fits = []
    for _, sy, ty in _axis(found_y, known_y):
        for _, sx, tx in _axis(found_x, known_x):
            sy2, ty2 = _refine(found_y, known_y, sy, ty)
            sx2, tx2 = _refine(found_x, known_x, sx, tx)
            fits.append(Fit(sx2, tx2, sy2, ty2, _covered(Fit(sx2, tx2, sy2, ty2, 0.0), rules, layout)))
    if not fits:
        return None
    best = max(fits, key=lambda f: f.share)
    near = max((f for f in fits if abs(f.tx) <= NEAR and abs(f.ty) <= NEAR), key=lambda f: f.share, default=None)
    chosen = best if near is None or best.share - near.share >= FAR_BETTER else near
    return chosen if chosen.share >= FIT else None


class _Page:
    """Just enough of a pdfplumber page for ``official.Grid``: the form's own ruling lines where the page has them."""

    def __init__(self, fit: Fit, layout: dict, width: float, height: float):
        self.width, self.height = width, height
        self.horizontal_edges = [{"top": fit.y(y), "x0": fit.x(x0), "x1": fit.x(x1)} for y, x0, x1, _ in layout["h"]]
        self.vertical_edges = [{"x0": fit.x(x), "top": fit.y(y0), "bottom": fit.y(y1)} for x, y0, y1 in layout["v"]]


def _labels(fit: Fit, layout: dict) -> list[dict]:
    return [{"text": t, "x0": fit.x(x0), "x1": fit.x(x1), "top": fit.y(top), "bottom": fit.y(bottom)} for t, x0, x1, top, bottom in layout["words"]]


# --- reading -----------------------------------------------------------------------------------------------------

def _distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            row.append(min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = row
    return previous[-1]


def _word(chunk: list[ocr.Token], scale: float, ox: float, oy: float) -> dict:
    """An OCR chunk as a word of the page, in points. ``scale`` and the offset undo the cell's cut and zoom."""
    chars = [{"text": t.text, "x0": (ox + t.x0 / scale) * PT, "x1": (ox + t.x1 / scale) * PT,
              "top": (oy + t.top / scale) * PT, "bottom": (oy + t.bottom / scale) * PT} for t in chunk]
    w = _make(chars)
    w["text"] = ocr._text(chunk, spaced=True)
    w["conf"] = min(t.conf for t in chunk)
    return w


# A cell that can only hold one kind of thing is read with only those characters allowed (Hangul and spaces in the list
# make Tesseract answer nothing at all, so a cell with a unit printed in it is cut around the unit instead).
WHITELIST = {"amount": "0123456789,", "code": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789."}
MARKS = {"년", "월", "일", "일부터"}  # what the form prints in a date cell


def _distinct(tokens: list[ocr.Token]) -> list[ocr.Token]:
    """Tesseract sometimes answers twice for one glyph (a syllable and a piece of it, written over each other); the
    smaller of two boxes that mostly overlap is the piece. A box much wider than its own letters is a box Tesseract
    drew round its neighbours too, and it does not cover them."""
    def area(t: ocr.Token) -> float:
        return max(1.0, (t.x1 - t.x0) * (t.bottom - t.top))

    def overlap(a: ocr.Token, b: ocr.Token) -> float:
        return max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0)) * max(0.0, min(a.bottom, b.bottom) - max(a.top, b.top))

    def tight(t: ocr.Token) -> bool:
        return t.x1 - t.x0 <= 1.4 * len(t.text) * (t.bottom - t.top)

    return [t for t in tokens if not any(u is not t and tight(u) and overlap(t, u) >= 0.6 * area(t) and (area(u), u.conf) > (area(t), t.conf) for u in tokens)]


def _read_cell(clean: Image.Image, region: tuple[float, float, float, float], whitelist: str | None = None, psm: int | None = None, inset: int = INSETS[0]) -> list[dict]:
    """The words in a cell (a box in points) of the cleaned page, with the OCR's confidence on each."""
    left, top, right, bottom = (int(round(v / PT)) for v in region)
    left, top = max(0, left + inset), max(0, top + inset)
    right, bottom = min(clean.width, right - inset), min(clean.height, bottom - inset)
    if right - left < 12 or bottom - top < 12:
        return []
    cut = clean.crop((left, top, right, bottom))
    grey = np.asarray(cut.filter(ImageFilter.GaussianBlur(1.2)), dtype=np.int16)  # blurred, so that the scanner's noise does not count as ink
    if (grey < np.median(grey) - 40).mean() < BLANK:
        return []  # nothing is written here; Tesseract would read the scanner's noise as text
    crop = cut.resize(((right - left) * ZOOM, (bottom - top) * ZOOM), Image.LANCZOS)
    pad = 16 * ZOOM
    page = Image.new("L", (crop.width + 2 * pad, crop.height + 2 * pad), 255)
    page.paste(crop, (pad, pad))
    single = (bottom - top) * PT < 26  # one printed line is about 12 points
    tokens = _distinct(ocr.recognise(page, psm=psm or (7 if single else 6), whitelist=whitelist))
    if single:
        for t in tokens:  # a syllable Tesseract put on a line of its own is still on this line, to the left or right of its neighbour
            t.line = (1, 1, 1)
    return [_word(c, ZOOM, left - pad / ZOOM, top - pad / ZOOM) for c in ocr.chunks(tokens)]


def _confidence(words: list[dict]) -> float:
    return min((w["conf"] for w in words if _n(w["text"]) not in HINTS and re.search(r"\w", w["text"])), default=0.0) / 100.0


HANGUL = re.compile(r"[가-힣]")


def _name(words: list[dict]) -> str | None:
    """A name or a title written in Hangul, from what the cell read. Words with no Hangul in them are what a blurred
    scan makes of a rule's remains ("|", "Ee Sass") and are dropped; what is left must be mostly Hangul and at least
    two syllables long, or the cell is treated as unreadable. A wrong name that looks like a name still gets through."""
    kept = [w["text"] for w in words if HANGUL.search(w["text"])]
    text = " ".join(kept).strip(" |.:;,'\"-_")
    letters = re.sub(r"\s", "", text)
    if len(HANGUL.findall(letters)) < 2 or len(HANGUL.findall(letters)) < 0.8 * len(letters):
        return None
    return text


def _whitelist(zone: Zone) -> str | None:
    return WHITELIST["code"] if zone.label == "질병분류기호:" else WHITELIST.get(zone.kind)


def _day(parts: list[str]) -> str | None:
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2])).isoformat() if all(parts) and len(parts[0]) == 4 else None
    except ValueError:
        return None  # a month 41 is a misread, not a date: nothing is better than that


def _date(clean: Image.Image, region: tuple[float, float, float, float], labels: list[dict], inset: int) -> tuple[str, list[dict]] | None:
    """A date written into a cell that prints 년, 월 and 일 in it, from two readings that must agree.

    The printed marks are painted out first. One reading takes the whole cell as a line and gives each run of digits to
    the mark that follows it; the other reads the digits between the marks one by one with only digits allowed. Each
    is wrong now and then, in different places, so a date is accepted only when both give it; otherwise the field is
    left empty and a person reads it."""
    marks = sorted((w for w in labels if _n(w["text"]) in MARKS and region[0] <= (w["x0"] + w["x1"]) / 2 <= region[2]
                    and region[1] <= (w["top"] + w["bottom"]) / 2 <= region[3]), key=lambda w: w["x0"])
    if len(marks) != 3:
        return None
    page = clean.copy()
    pen = ImageDraw.Draw(page)
    for m in marks:
        pen.rectangle([(m["x0"] - 1) / PT, (m["top"] - 1) / PT, (m["x1"] + 1) / PT, (m["bottom"] + 1) / PT], fill=255)
    centres = [(m["x0"] + m["x1"]) / 2 for m in marks]
    words = _read_cell(page, region, inset=inset)  # no character list: with one, Tesseract runs "2026 10 18" together as "20261018"
    whole = ["", "", ""]
    for w in sorted(words, key=lambda w: w["x0"]):
        k = sum((w["x0"] + w["x1"]) / 2 > c for c in centres)
        if k < 3:
            whole[k] += re.sub(r"\D", "", w["text"].translate(ocr.DIGIT_FIX))
    first = _day(whole)
    if first is None:
        return None
    between, found = [], list(words)
    for a, b in zip([region[0]] + centres[:2], centres):
        got = _read_cell(page, (a, region[1], b, region[3]), "0123456789", inset=inset)
        between.append("".join(re.sub(r"\D", "", w["text"]) for w in got))
        found += got
    return (first, found) if _day(between) == first else None


def _printed(clean: Image.Image, anchor: list[dict], zone: Zone) -> bool:
    """Whether the label the form should have here reads as that label (a few characters off is still it)."""
    x0, top, x1, bottom = _box(anchor)
    words = _read_cell(clean, (x0 - 3, top - 2, x1 + 3, bottom + 2))
    text = _n("".join(w["text"] for w in words))
    n = len(zone.anchor)
    allowed = 0 if n <= 4 else 1 if n <= 8 else 2
    return any(_distance(text[i:i + k], zone.anchor) <= allowed for k in {n - 1, n, n + 1} if k > 0 for i in range(max(1, len(text) - k + 1)))


def _cell_value(clean: Image.Image, zone: Zone, region: tuple[float, float, float, float], labels: list[dict], inset: int) -> tuple[str | None, list[dict]]:
    """What one cell holds, as the kind of value the zone asks for, and the words it was read from."""
    if zone.kind == "date":
        read = _date(clean, region, labels, inset)
        return read if read else (None, [])
    words = _read_cell(clean, region, _whitelist(zone), inset=inset)
    if not words:
        return None, []
    return (_name(words) if zone.kind == "text" and zone.label != "질병분류기호:" else _value(zone.kind, words, zone.ignore)), words


def _wiped(image: Image.Image, page: _Page) -> Image.Image:
    """The page with the form's own ruling lines painted out, where the registration puts them. (Painting out the lines
    that were found would also paint out the stems and bars of letters that a faint-line detector takes for rules.)"""
    clean = image.copy()
    pen = ImageDraw.Draw(clean)
    for e in page.horizontal_edges:
        pen.line([(e["x0"] / PT, e["top"] / PT), (e["x1"] / PT, e["top"] / PT)], fill=255, width=WIPE)
    for e in page.vertical_edges:
        pen.line([(e["x0"] / PT, e["top"] / PT), (e["x0"] / PT, e["bottom"] / PT)], fill=255, width=WIPE)
    return clean


def read_image(image: Image.Image, page_no: int, doc_id: str) -> list[tuple[str, str, EvidenceRef, float]] | None:
    """(label, value, evidence, confidence 0..1) for each filled field of an official form on a page picture,
    or None when the picture is not one (its lines do not lie on the form's grid, or its labels do not read)."""
    if not ocr.available():
        return None
    image = straighten(image.convert("L"))
    rules = find_rules(image)
    if len(rules.horizontal) < MIN_RULES:
        return None
    fits = {kind: register(rules, kind) for kind in TEMPLATES}
    kind = max((k for k in fits if fits[k]), key=lambda k: fits[k].share, default=None)
    if kind is None:
        return None
    fit, layout = fits[kind], LAYOUTS[kind]
    page = _Page(fit, layout, image.width * PT, image.height * PT)
    clean = _wiped(image, page)
    rows, grid, zones = _rows(_labels(fit, layout)), Grid(page), TEMPLATES[kind][1]
    present = [(zone, anchor) for zone in zones if (anchor := _find(rows, zone.anchor))]
    checked = sorted(present, key=lambda p: -len(p[0].anchor))[:LABELS_CHECKED]
    if not checked or sum(_printed(clean, a, z) for z, a in checked) < LABELS_READ * len(checked):
        return None
    out = []
    labels = _labels(fit, layout)
    for zone, anchor in present:
        region = _region(grid, anchor, zone.where)
        for inset in INSETS:
            value, words = _cell_value(clean, zone, region, labels, inset)
            if value is not None:
                break
        if value is None:
            continue
        x0, top, x1, bottom = _box(anchor + words)
        bbox = (x0 / page.width, top / page.height, x1 / page.width, bottom / page.height)
        out.append((zone.label, value, EvidenceRef(doc_id=doc_id, page=page_no, bbox=bbox, snippet=f"{zone.label} {value}"), _confidence(words)))
    return out
