"""Read claim documents field by field, keeping where each value was found.

The deterministic reader keys on the form labels (``환자성명:`` …) exactly like the
drawing extractor keys on ``HOLE_SPACING:``: a label and the value words to its right on
the same line, with the group's bounding box as evidence. Dates in any of the accepted
formats are normalised to ISO; anything that does not parse is kept verbatim so the
validator can flag it instead of a silent guess. A model-based reader returns values
with the literal snippet it read; ``ground`` then accepts a value only if that snippet
is really in the document, and attaches the bbox where it is.
"""

from __future__ import annotations

from io import BytesIO
import re
import pdfplumber
from fitwitness.claims.models import EvidenceRef, Extraction

# label -> (field, document kinds where it is authoritative)
LABELS: dict[str, str] = {
    "환자성명:": "insured_name", "성명:": "insured_name", "환자:": "insured_name", "환자명:": "insured_name",
    "병명:": "diagnosis_name", "질병분류기호:": "diagnosis_code", "진단일:": "diagnosis_date",
    "입원일:": "admission_date", "퇴원일:": "discharge_date",
    "수술명:": "surgery_name", "수술일:": "surgery_date", "수술분류:": "surgery_grade", "관련진단:": "diagnosis_code",
    "합계:": "total_amount", "발행기관:": "hospital",
}
# When two documents disagree, the one that is authoritative for the field wins.
AUTHORITY = {"diagnosis_code": "diagnosis", "diagnosis_name": "diagnosis", "diagnosis_date": "diagnosis",
             "admission_date": "admission", "discharge_date": "admission",
             "surgery_name": "surgery", "surgery_date": "surgery", "surgery_grade": "surgery",
             "total_amount": "receipt", "insured_name": "diagnosis", "hospital": "diagnosis"}
# What a payment depends on. A scanner's doubt about one of these is a reason for a person to look
# (R-CONF-01); its doubt about a hospital's name or a procedure's wording is not, since nothing is paid
# on them. (The synthetic policies carry no name to compare the insured's against; a real system would
# add insured_name here.)
DECISIVE = {"diagnosis_code", "diagnosis_date", "admission_date", "discharge_date", "surgery_date", "surgery_grade", "total_amount"}
DATE_RES = [
    re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"), re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})$"),
    re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$"), re.compile(r"^(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일$"),
]


def normalise_date(text: str) -> str | None:
    t = text.strip()
    for rx in DATE_RES:
        m = rx.match(t)
        if m:
            y, mo, d = (int(x) for x in m.groups())
            if 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def normalise_amount(text: str) -> int | None:
    digits = re.sub(r"[^0-9]", "", text)
    return int(digits) if digits else None


def _kind(field: str) -> str:
    """What kind of value a field holds, for the OCR reader's rule on how many words it may span."""
    return ("date" if field.endswith("_date") else "code" if field == "diagnosis_code" else "amount" if field == "total_amount"
            else "grade" if field == "surgery_grade" else "name")


def _text_pairs(page, n: int, doc_id: str) -> list[tuple[str, str, EvidenceRef]]:
    pairs = []
    rows: dict[int, list] = {}
    for w in page.extract_words():
        rows.setdefault(round(w["top"] / 3), []).append(w)
    for row in rows.values():
        row.sort(key=lambda w: w["x0"])
        i = 0
        while i < len(row):
            if not row[i]["text"].endswith(":"):
                i += 1
                continue
            label, start = row[i]["text"], i
            i += 1
            values = []
            while i < len(row) and not row[i]["text"].endswith(":"):
                values.append(row[i]["text"])
                i += 1
            if not values:
                continue
            group = row[start:i]
            bbox = (min(w["x0"] for w in group) / page.width, min(w["top"] for w in group) / page.height,
                    max(w["x1"] for w in group) / page.width, max(w["bottom"] for w in group) / page.height)
            text = " ".join(values)
            pairs.append((label, text, EvidenceRef(doc_id=doc_id, page=n, bbox=bbox, snippet=f"{label} {text}")))
    return pairs


def read_pairs_scored(data: bytes, doc_id: str) -> list[tuple[str, str, EvidenceRef, float, str]]:
    """(label, value text, evidence, confidence, source) for every ``label: value`` group.

    A page with a text layer is read exactly (confidence 1.0, source "text"): by its grid when it is
    one of the official forms (``claims/official.py``), otherwise as ``label: value`` lines. A page without one
    is a scan: it is read by OCR (``claims/ocr.py``), source "ocr", with the reader's own
    confidence per value. Without an OCR engine a scanned page yields nothing."""
    out = []
    with pdfplumber.open(BytesIO(data)) as pdf:
        for n, page in enumerate(pdf.pages, 1):
            if page.extract_words():
                from fitwitness.claims import official

                grid = official.read_page(page, n, doc_id)
                pairs = _text_pairs(page, n, doc_id) if grid is None else grid
                out += [(label, text, ref, 1.0, "text") for label, text, ref in pairs]
            else:
                from fitwitness.claims import ocr

                out += [(label, text, ref, conf, "ocr") for label, text, ref, conf in ocr.read_pairs_ocr(
                    data, doc_id, n - 1, {**{label: _kind(field) for label, field in LABELS.items()}, **dict.fromkeys(ocr.PART_LABELS, "amount")})]
    return out


def read_pairs(data: bytes, doc_id: str) -> list[tuple[str, str, EvidenceRef]]:
    """(label, value text, evidence) for every ``label: value`` group in the PDF."""
    return [(label, text, ref) for label, text, ref, _, _ in read_pairs_scored(data, doc_id)]


def extract_documents(documents: list[tuple[str, str, bytes]]) -> Extraction:
    """documents: (doc_id, kind, pdf bytes). Deterministic, no model."""
    from fitwitness.claims import ocr

    values: dict[str, tuple[str, EvidenceRef, bool, float, str]] = {}  # field -> (raw, evidence, authoritative, confidence, source)
    origin: dict[str, str] = {}  # field -> the document its value was read from
    lines: dict[str, list[str]] = {}  # document -> the receipt lines that add up to its total (scans only)
    problems = 0
    for doc_id, kind, data in documents:
        for label, text, ref, conf, source in read_pairs_scored(data, doc_id):
            if source == "ocr" and label in ocr.PART_LABELS:
                lines.setdefault(doc_id, []).append(text)
                continue
            field = LABELS.get(label)
            if not field:
                continue
            authoritative = AUTHORITY.get(field) == kind
            if field in values and values[field][2] and not authoritative:
                continue  # keep the authoritative document's value
            values[field] = (text, ref, authoritative, conf, source)
            origin[field] = doc_id
    out: dict = {}
    evidence: dict[str, EvidenceRef] = {}
    floor = 1.0
    for field, (raw, ref, _, conf, source) in values.items():
        confirmed = False
        if source == "ocr":  # a scanner confuses letters with digits; fix only where the field's shape demands a digit
            raw = ocr.repair_date(raw) if field.endswith("_date") else ocr.repair_code(raw) if field == "diagnosis_code" \
                else ocr.repair_amount(raw) if field == "total_amount" else ocr.repair_grade(raw) if field == "surgery_grade" else raw
        if field.endswith("_date"):
            iso = normalise_date(raw)
            if iso is None:
                problems += 1
            out[field] = iso or raw  # unparseable stays verbatim for the validator to flag
        elif field == "surgery_grade":
            m = re.search(r"(\d)\s*종", raw)
            if m:
                out[field] = int(m.group(1))
            else:
                problems += 1
                continue
        elif field == "total_amount":
            amount, confirmed = ocr.checked_amount(raw, lines.get(origin[field], [])) if source == "ocr" else (normalise_amount(raw), False)
            if amount is None:
                problems += 1
                continue
            out[field] = amount
        else:
            out[field] = raw
        evidence[field] = ref
        if field in DECISIVE and not confirmed:  # a total the receipt's own lines add up to needs no further doubt
            floor = min(floor, conf)
    # The least certain value limits what the whole extraction can be trusted for.
    confidence = max(0.0, min(1.0 - 0.2 * problems, floor))
    return Extraction(**out, evidence=evidence, confidence=confidence, scanned=any(v[4] == "ocr" for v in values.values()))


def ground(candidate: dict, documents: list[tuple[str, str, bytes]]) -> Extraction:
    """Accept a model's extraction only where its quoted snippet exists in a document.

    ``candidate`` is {field: {"value": ..., "snippet": "..."}} as a model would return it.
    A field whose snippet cannot be found is dropped, not trusted, and lowers confidence."""
    pairs = [(doc_id, label, text, ref) for doc_id, _, data in documents for label, text, ref in read_pairs(data, doc_id)]
    out: dict = {}
    evidence: dict[str, EvidenceRef] = {}
    dropped = 0
    for field, item in candidate.items():
        if field not in Extraction.FIELDS or not isinstance(item, dict):
            continue
        snippet = str(item.get("snippet", "")).strip()
        hit = next((ref for _, label, text, ref in pairs if snippet and snippet in f"{label} {text}"), None)
        if hit is None:
            dropped += 1
            continue
        value = item.get("value")
        if field.endswith("_date") and isinstance(value, str):
            value = normalise_date(value) or value
        out[field] = value
        evidence[field] = hit
    return Extraction(**out, evidence=evidence, confidence=max(0.0, 1.0 - 0.2 * dropped))
