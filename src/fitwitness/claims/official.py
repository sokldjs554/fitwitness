"""Read the official Korean medical forms by their grid, not by "label: value" text.

The claim documents the generator makes put a label, a colon and a value on one line. The forms a
hospital actually issues do not: the diagnosis certificate (의료법 시행규칙 별지 제5호의2서식) and the
medical-fee statement (국민건강보험 요양급여의 기준에 관한 규칙 별지 제6호서식) are tables, labels carry no
colon, a value sits in the cell next to its label (or under it), and the form prints its own hints inside
the value cell ("년 월 일부터"). Reading `입원일: 년 월 일부터` as a value was the first thing the text reader
did with an empty certificate.

So a page is recognised by the printed labels it must contain, each label is found by its text, the cell
around it is taken from the page's own ruling lines, and the value is whatever words the hospital put in
the cell it points to, minus the hints the form prints there. The result is the same ``(label, value,
evidence)`` triples the text reader returns, so everything after extraction is unchanged.

A page that does not carry the labels is not an official form and is left to the text reader.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fitwitness.claims.models import EvidenceRef

# The forms spell the same middle dot in several ways; circled digits number the amounts and are not part of a label.
_NORMALISE = str.maketrans({"·": "ㆍ", "・": "ㆍ", "･": "ㆍ", "•": "ㆍ", "∙": "ㆍ", **{chr(0x2460 + i): "" for i in range(20)}})
HINTS = {"년", "월", "일", "일부터", "부터", "까지", "(", ")", "[", "]"}


def _n(text: str) -> str:
    return re.sub(r"\s+", "", text).translate(_NORMALISE)


@dataclass(frozen=True)
class Zone:
    anchor: str  # the printed label, spaces removed
    where: str  # right: the next cell | after: the rest of the line | below_in: under the label in its cell | below_cell: the cell under
    label: str  # what the value is reported as: a label the text reader's table knows
    kind: str  # text | date | amount
    ignore: tuple[str, ...] = ()  # words the form prints inside that value cell


TEMPLATES = {
    "diagnosis": (
        ("환자의성명", "질병분류기호", "진단연월일"),
        (
            Zone("환자의성명", "right", "환자성명:", "text"),
            Zone("(주질병ㆍ부상)", "after", "병명:", "text"),
            Zone("질병분류기호", "below_in", "질병분류기호:", "text"),
            Zone("진단연월일", "right", "진단일:", "date"),
            Zone("입원일:", "after", "입원일:", "date"),
            Zone("퇴원일:", "after", "퇴원일:", "date"),
            Zone("의료기관명칭:", "after", "발행기관:", "text"),
        ),
    ),
    "receipt": (
        ("진료비총액", "환자성명", "질병군(DRG)번호"),  # the DRG box tells the 별지 제6호 from the 간이 제12호, which has no such cell
        (
            Zone("환자성명", "below_cell", "환자성명:", "text"),
            Zone("진료비총액", "right", "합계:", "amount"),
            Zone("상호", "right", "발행기관:", "text"),
        ),
    ),
}


class Grid:
    """The ruling lines of a page, and the cell that holds a point."""

    def __init__(self, page):
        self.width, self.height = float(page.width), float(page.height)
        self.h = sorted({(round(float(e["top"]), 1), round(float(e["x0"]), 1), round(float(e["x1"]), 1)) for e in page.horizontal_edges})
        self.v = sorted({(round(float(e["x0"]), 1), round(float(e["top"]), 1), round(float(e["bottom"]), 1)) for e in page.vertical_edges})

    def cell(self, cx: float, cy: float) -> tuple[float, float, float, float]:
        top = max((y for y, x0, x1 in self.h if y <= cy - 1 and x0 - 1 <= cx <= x1 + 1), default=0.0)
        bottom = min((y for y, x0, x1 in self.h if y >= cy + 1 and x0 - 1 <= cx <= x1 + 1), default=self.height)
        left = max((x for x, y0, y1 in self.v if x <= cx - 1 and y0 - 1 <= cy <= y1 + 1), default=0.0)
        right = min((x for x, y0, y1 in self.v if x >= cx + 1 and y0 - 1 <= cy <= y1 + 1), default=self.width)
        return left, top, right, bottom


def _words(page) -> list[dict]:
    """The page's words. A form prints spaces between its 년 월 일 to make room; a value written over them
    would be cut into single characters, so a space that a visible character sits on is dropped first."""
    solid: dict[int, list[dict]] = {}
    for c in page.chars:
        if c["text"].strip():
            solid.setdefault(round(c["top"] / 4), []).append(c)

    def covered(s: dict) -> bool:
        width = s["x1"] - s["x0"]
        near = (c for k in (-1, 0, 1) for c in solid.get(round(s["top"] / 4) + k, ()))
        return any(abs(c["top"] - s["top"]) < 4 and min(c["x1"], s["x1"]) - max(c["x0"], s["x0"]) > 0.3 * width for c in near)

    dropped = {(round(c["x0"], 2), round(c["top"], 2)) for c in page.chars if not c["text"].strip() and covered(c)}
    return page.filter(lambda o: o.get("object_type") != "char" or (round(o["x0"], 2), round(o["top"], 2)) not in dropped or o["text"].strip()).extract_words(return_chars=True)


def _make(chars: list[dict]) -> dict:
    return {"text": "".join(c["text"] for c in chars), "x0": min(c["x0"] for c in chars), "x1": max(c["x1"] for c in chars),
            "top": min(c["top"] for c in chars), "bottom": max(c["bottom"] for c in chars), "chars": chars}


def _cut(word: dict, k: int) -> tuple[dict, dict] | None:
    """The word split after its first ``k`` normalised characters, or None when nothing follows them."""
    used = 0
    for i, ch in enumerate(word["chars"]):
        used += len(_n(ch["text"]))
        if used >= k:
            return (_make(word["chars"][: i + 1]), _make(word["chars"][i + 1:])) if word["chars"][i + 1:] else None
    return None


def _centre(w: dict) -> float:
    return (w["top"] + w["bottom"]) / 2


def _rows(words: list[dict]) -> list[list[dict]]:
    """Words grouped into lines: a word joins the line whose centre is within half a line of its own. A value a
    clerk or a printer sets a few points high or low still lands on its label's line; the forms' own lines are
    twelve points apart or more."""
    rows: list[list[dict]] = []
    for w in sorted(words, key=_centre):
        for row in rows:
            if abs(sum(_centre(x) for x in row) / len(row) - _centre(w)) < 5.0:
                row.append(w)
                break
        else:
            rows.append([w])
    for row in rows:
        row.sort(key=lambda w: w["x0"])
    return sorted(rows, key=lambda row: _centre(row[0]))


def _locate(rows: list[list[dict]], anchor: str) -> tuple[list[dict], int] | None:
    """The words of the first line that spells ``anchor`` (spaces ignored), and how many normalised characters
    of the last of them belong to it."""
    for row in rows:
        text, spans = "", []
        for w in row:
            t = _n(w["text"])
            spans.append((len(text), len(text) + len(t), w))
            text += t
        i = text.find(anchor)
        if i >= 0:
            hit = [(s, w) for s, e, w in spans if e > i and s < i + len(anchor)]
            return [w for _, w in hit], i + len(anchor) - hit[-1][0]
    return None


def _find(rows: list[list[dict]], anchor: str) -> list[dict] | None:
    hit = _locate(rows, anchor)
    return hit[0] if hit else None


def _separate(words: list[dict], anchors: list[str]) -> list[dict]:
    """A value written close to its label comes out of the PDF as one word with it ("명칭:서울병원"); cut each
    label's last word where the label ends, so the value is a word of its own."""
    words = list(words)
    for anchor in anchors:
        hit = _locate(_rows(words), anchor)
        if not hit:
            continue
        last, consumed = hit[0][-1], hit[1]
        parts = _cut(last, consumed) if consumed < len(_n(last["text"])) else None
        if parts:
            i = next(i for i, w in enumerate(words) if w is last)
            words[i:i + 1] = list(parts)
    return words


def _box(words: list[dict]) -> tuple[float, float, float, float]:
    return min(w["x0"] for w in words), min(w["top"] for w in words), max(w["x1"] for w in words), max(w["bottom"] for w in words)


def _inside(words: list[dict], region: tuple[float, float, float, float], skip: set[int]) -> list[dict]:
    out = []
    for w in words:
        cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
        if id(w) not in skip and region[0] <= cx <= region[2] and region[1] <= cy <= region[3]:
            out.append(w)
    return [w for row in _rows(out) for w in row]


def _region(grid: Grid, anchor: list[dict], where: str) -> tuple[float, float, float, float]:
    ax0, atop, ax1, abottom = _box(anchor)
    cx, cy = (ax0 + ax1) / 2, (atop + abottom) / 2
    left, top, right, bottom = grid.cell(cx, cy)
    if where == "right":
        return grid.cell(right + 2, cy)
    if where == "after":
        return ax1 + 0.5, atop - 2, right, abottom + 2
    if where == "below_in":
        return left, abottom, right, bottom
    if where == "below_cell":
        return grid.cell(cx, bottom + 2)
    raise ValueError(where)


def _value(kind: str, words: list[dict], ignore: tuple[str, ...]) -> str | None:
    tokens = [w["text"] for w in words if _n(w["text"]) not in ignore and _n(w["text"]) not in HINTS]
    text = " ".join(tokens).strip()
    if kind == "date":
        numbers = re.findall(r"\d+", re.sub(r"부터|까지|[년월일]", " ", " ".join(w["text"] for w in words)))
        if len(numbers) >= 3 and len(numbers[0]) == 4:
            return f"{numbers[0]}-{int(numbers[1]):02d}-{int(numbers[2]):02d}"
        return None
    if kind == "amount":
        digits = re.sub(r"[^\d,]", "", text).strip(",")
        return digits or None
    return text or None


def read_page(page, page_no: int, doc_id: str) -> list[tuple[str, str, EvidenceRef]] | None:
    """(label, value, evidence) for each filled field of an official form, or None when the page is not one."""
    words = _words(page)
    if not words:
        return None
    rows = _rows(words)
    for name, (required, zones) in TEMPLATES.items():
        if all(_find(rows, a) for a in required):
            break
    else:
        return None
    words = _separate(words, [zone.anchor for zone in zones])
    rows = _rows(words)
    grid = Grid(page)
    out = []
    for zone in zones:
        anchor = _find(rows, zone.anchor)
        if not anchor:
            continue
        found = _inside(words, _region(grid, anchor, zone.where), {id(w) for w in anchor})
        value = _value(zone.kind, found, zone.ignore) if found else None
        if value is None:
            continue
        x0, top, x1, bottom = _box(anchor + found)
        bbox = (x0 / page.width, top / page.height, x1 / page.width, bottom / page.height)
        out.append((zone.label, value, EvidenceRef(doc_id=doc_id, page=page_no, bbox=bbox, snippet=f"{zone.label} {value}")))
    return out
