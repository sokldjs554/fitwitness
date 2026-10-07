"""Read a scanned claim document, one without a text layer, with Tesseract.

A claim that arrives as a photo or a fax is a PDF whose pages are pictures. The deterministic
reader (``extract.read_pairs``) finds no words in it. This module renders the page, runs
Tesseract (Korean + English), and rebuilds the same ``label: value`` groups with the same
page-normalised evidence boxes, plus one thing a text layer never has: a confidence per value.

Tesseract returns Hangul one syllable at a time ("입" "원" "일" ":"), so tokens on a line are
first merged into chunks wherever the gap is smaller than a fraction of the line height; a
label is then a chunk (or two) ending in a colon that is one of the form's known labels. The
confidence of a field is the lowest confidence of the tokens it was read from, and
``extract_documents`` carries it into the extraction, where a low value sends the claim to a
person (rule R-CONF-01) instead of paying on a number the reader was not sure of.

Needs the ``tesseract`` binary with the ``kor`` language data (``apt install tesseract-ocr
tesseract-ocr-kor``). Without it a scanned page simply yields no pairs, the validator flags the
missing fields and the claim goes to review; nothing is guessed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

DPI = 200
MERGE_GAP = float(os.getenv("FITWITNESS_OCR_MERGE_GAP", "0.60"))  # tokens closer than this (x line height) are one word
SPACE_GAP = 0.40  # inside a word of a value, a gap this wide is a space ("급성 심근경색")
MIN_CONF = 25.0  # tokens below this are scanner noise, not text
STRAY_CONF = 45.0  # a value stops at a word read this poorly: it is a smudge next to the value, not part of it
COLUMN_GAP = 1.6  # a gap this wide (x line height) after a value starts another column
# How many words each kind of value can have; the rest of the line belongs to something else.
FIELD_WORDS = {"date": 1, "code": 1, "amount": 2, "grade": 1, "name": 8}
COLONS = ":;：﹕"
# Labels that end a value on a form without being fields we read.
STOP_LABELS = {"문서번호", "발급일", "진료과", "담당의", "면허번호", "발행기관", "주소", "용도", "작성일", "병원", "비고"}
# Characters Tesseract confuses with digits, applied only where a digit is required.
DIGIT_FIX = str.maketrans({"O": "0", "o": "0", "Q": "0", "D": "0", "I": "1", "l": "1", "|": "1", "i": "1", "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2", "g": "9"})


@dataclass
class Token:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    conf: float
    line: tuple[int, int, int]


def binary() -> str | None:
    return shutil.which(os.getenv("FITWITNESS_TESSERACT") or "tesseract")


def available() -> bool:
    return binary() is not None


def render(pdf: bytes, page_index: int, dpi: int = DPI):
    import pypdfium2 as pdfium

    page = pdfium.PdfDocument(pdf)[page_index]
    return page.render(scale=dpi / 72).to_pil().convert("L")


def recognise(image, *, lang: str | None = None, timeout: float = 60.0) -> list[Token]:
    exe = binary()
    if exe is None:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "page.png"
        image.save(path)
        # One thread per Tesseract: it would otherwise use every core, and several readers at once (the
        # evaluation runs four) starve each other until they time out.
        env = {**os.environ, "OMP_THREAD_LIMIT": os.getenv("FITWITNESS_OCR_THREADS", "1")}
        try:
            out = subprocess.run([exe, str(path), "-", "-l", lang or os.getenv("FITWITNESS_OCR_LANG", "kor+eng"), "--psm", "6", "tsv"],
                                 capture_output=True, text=True, timeout=timeout, env=env).stdout
        except subprocess.TimeoutExpired:
            return []  # a page that cannot be read is a page with nothing on it: the claim goes to a person
    tokens = []
    for row in out.splitlines()[1:]:
        f = row.split("\t")
        if len(f) != 12 or not f[11].strip() or float(f[10]) < MIN_CONF:
            continue
        left, top, w, h = (int(f[i]) for i in (6, 7, 8, 9))
        tokens.append(Token(f[11].strip(), left, left + w, top, top + h, float(f[10]), (int(f[2]), int(f[3]), int(f[4]))))
    return tokens


def chunks(tokens: list[Token]) -> list[list[Token]]:
    """Group tokens into words: same line, gap below MERGE_GAP of the line height."""
    lines: dict[tuple, list[Token]] = {}
    for t in tokens:
        lines.setdefault(t.line, []).append(t)
    out: list[list[Token]] = []
    for line in sorted(lines.values(), key=lambda ts: min(t.top for t in ts)):
        line.sort(key=lambda t: t.x0)
        height = sorted(t.bottom - t.top for t in line)[len(line) // 2]
        current = [line[0]]
        for t in line[1:]:
            if t.x0 - current[-1].x1 < MERGE_GAP * height:
                current.append(t)
            else:
                out.append(current)
                current = [t]
        out.append(current)
    return out


def _text(chunk: list[Token], spaced: bool = False) -> str:
    out = chunk[0].text
    for a, b in zip(chunk, chunk[1:]):
        height = max(a.bottom - a.top, b.bottom - b.top)
        out += (" " if spaced and b.x0 - a.x1 >= SPACE_GAP * height else "") + b.text
    return out.translate({ord(c): ":" for c in COLONS[1:]}) if out and out[-1] in COLONS else out


def _is_label(text: str) -> bool:
    return bool(text) and text[-1] == ":"


def _same_line(a: list[Token], b: list[Token]) -> bool:
    height = max(t.bottom - t.top for t in a + b)
    return abs(min(t.top for t in a) - min(t.top for t in b)) < height


def pairs(tokens: list[Token], doc_id: str, page_no: int, size: tuple[int, int], labels: dict[str, str]):
    """(label with colon, value text, evidence box, confidence 0..1) for every known label on the page."""
    from fitwitness.claims.models import EvidenceRef

    words = chunks(tokens)
    texts = [_text(w) for w in words]
    known = set(labels) | {s + ":" for s in STOP_LABELS}
    # 1. where the labels are: a chunk ending in a colon, or two chunks that together form a known label
    found: list[tuple[int, int, str]] = []  # first chunk, last chunk, label
    for j, text in enumerate(texts):
        if not _is_label(text):
            continue
        if j > 0 and not _is_label(texts[j - 1]) and _same_line(words[j - 1], words[j]) and (texts[j - 1] + text) in known \
                and not (found and found[-1][1] >= j - 1):
            found.append((j - 1, j, texts[j - 1] + text))
        else:
            found.append((j, j, text))
    # 2. a label's value is what follows it on the line, up to the next label
    out = []
    for k, (first, last, label) in enumerate(found):
        if label not in labels:
            continue
        stop = found[k + 1][0] if k + 1 < len(found) else len(words)
        values: list[list[Token]] = []
        for w in words[last + 1:stop]:
            if not _same_line(words[last], w):
                continue
            previous = (values[-1] if values else words[last])[-1]
            height = max(t.bottom - t.top for t in w + [previous])
            # The value starts wherever the form puts it (the label column is far to its left); once it has
            # started, a poorly read word, a wide gap or enough words ends it.
            if values and (len(values) >= FIELD_WORDS[labels[label]] or min(t.conf for t in w) < STRAY_CONF
                           or w[0].x0 - previous.x1 > COLUMN_GAP * height):
                break
            values.append(w)
        if not values:
            continue
        used = [t for chunk in words[first:last + 1] + values for t in chunk]
        value = " ".join(_text(c, spaced=True) for c in values)
        width, height = size
        box = (min(t.x0 for t in used) / width, min(t.top for t in used) / height,
               max(t.x1 for t in used) / width, max(t.bottom for t in used) / height)
        out.append((label, value, EvidenceRef(doc_id=doc_id, page=page_no, bbox=box, snippet=f"{label} {value}"), min(t.conf for t in used) / 100.0))
    return out


def read_pairs_ocr(pdf: bytes, doc_id: str, page_index: int, labels: dict[str, str]):
    if not available():
        return []
    image = render(pdf, page_index)
    return pairs(recognise(image), doc_id, page_index + 1, image.size, labels)


# --- value repair: only where the field's shape says what a character must be -------------
_DIGITS = "0-9OoQDIil|SsBZzg"
_DATE = re.compile(rf"^([{_DIGITS}]{{4}})\s*([-./,년])\s*([{_DIGITS}]{{1,2}})\s*([-./,월])\s*([{_DIGITS}]{{1,2}})\s*(일?)$")
# A zero at the head of a code could be an O, a D or a Q, so it is not repaired: "005.1" stays as read, fails the
# code's shape, and a person looks at it. (Reading it as "O05.1" turned a covered D05.1 into "not covered".)
LEADING_LETTER = str.maketrans({"1": "I", "5": "S", "8": "B", "2": "Z", "|": "I"})


def repair_date(text: str) -> str:
    m = _DATE.match(text.strip())
    if not m:
        return text
    y, s1, mo, s2, d, tail = m.groups()
    sep = lambda c: "." if c == "," else c  # noqa: E731 - a comma between date parts is a misread full stop
    return f"{y.translate(DIGIT_FIX)}{sep(s1)}{mo.translate(DIGIT_FIX)}{sep(s2)}{d.translate(DIGIT_FIX)}{tail}"


def repair_code(text: str) -> str:
    """A KCD code is one letter, two digits, optionally a point and more digits."""
    t = text.strip().replace(",", ".").replace(" ", "")
    m = re.match(rf"^([A-Za-z{{1}}0-9|])([{_DIGITS}]{{2}})(\.?)([{_DIGITS}]*)$", t)
    if not m:
        return text
    head, num, dot, tail = m.groups()
    head = head.translate(LEADING_LETTER).upper()
    return f"{head}{num.translate(DIGIT_FIX)}{dot}{tail.translate(DIGIT_FIX)}"


def repair_grade(text: str) -> str:
    """"3종" is one digit and one syllable; the syllable is read as an 8 or a B often enough to matter."""
    m = re.fullmatch(r"\s*([1-5])\s*[8Bb]\s*", text)
    return f"{m.group(1)}종" if m else text


_NOISE = "!?~`'\"*#^_:;·•ㆍ"  # marks a scanner leaves next to a value


def repair_amount(text: str) -> str:
    """Letters read for digits are turned back, and specks of noise around the number are dropped."""
    t = text.strip().strip(_NOISE + " ")
    return t.translate(DIGIT_FIX) if re.fullmatch(r"[0-9OoIlSBZ|,.\s금원정]+", t) else t


_AMOUNT = re.compile(r"^(?:금\s*)?(\d{1,3}(?:,\d{3})+|\d+)\s*(?:원\s*정?)?$")
# A number the scanner finished with something that is probably the unit: "2,909,191" then "원" read as "2!".
_GROUPED_THEN_NOISE = re.compile(r"^(?:금\s*)?(\d{1,3}(?:,\d{3})+)([^,.]{1,3})$")
_PLAIN_THEN_NOISE = re.compile(r"^(?:금\s*)?(\d{4,})([^\d,.]{1,3})$")


def amount_value(text: str) -> int | None:
    """The number in an amount, only if it is written the way an amount is: thousands groups of
    three digits, or no groups at all, with the unit (원) at the end. A digit that was really a
    smudge ("2,909,1912!") breaks the grouping, so the amount is refused instead of paid."""
    m = _AMOUNT.match(text.strip())
    return int(m.group(1).replace(",", "")) if m else None


def amount_guess(text: str) -> int | None:
    """The number in front of an unreadable unit. Only a guess: the caller must have a second reason to
    believe it (see ``checked_amount``). A grouped number ends after its third digit, so whatever
    follows is the unit; a plain number may only be followed by non-digits, or the unit's glyph
    read as a digit would be added to it."""
    t = text.strip()
    m = _GROUPED_THEN_NOISE.match(t) or _PLAIN_THEN_NOISE.match(t)
    return int(m.group(1).replace(",", "")) if m else None


PART_LABELS = ("급여본인부담금:", "비급여:")  # the two lines of a receipt that add up to its total


def checked_amount(total: str, parts: list[str]) -> tuple[int | None, bool]:
    """The receipt's total, cross-checked the way a clerk would: the two lines above it must add up.

    Returns (amount, confirmed).

    * both lines readable and the total matches their sum: accepted and confirmed, even when the unit
      after the number was misread (the sum is a second, independent reading of the same number);
    * both lines readable and the total does not match: refused (one of the three is misread);
    * the lines cannot be read: the total is accepted, unconfirmed, only when it is written like an amount."""
    strict = amount_value(total)
    values = [amount_value(r) or amount_guess(r) for r in map(repair_amount, parts)]
    if len(values) == 2 and all(v is not None for v in values):
        wanted = sum(values)
        return (wanted, True) if (strict if strict is not None else amount_guess(total)) == wanted else (None, False)
    return strict, False
