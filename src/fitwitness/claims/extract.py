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


def read_pairs(data: bytes, doc_id: str) -> list[tuple[str, str, EvidenceRef]]:
    """(label, value text, evidence) for every ``label: value`` group in the PDF."""
    pairs = []
    with pdfplumber.open(BytesIO(data)) as pdf:
        for n, page in enumerate(pdf.pages, 1):
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


def extract_documents(documents: list[tuple[str, str, bytes]]) -> Extraction:
    """documents: (doc_id, kind, pdf bytes). Deterministic, no model."""
    values: dict[str, tuple[str, EvidenceRef, bool]] = {}  # field -> (raw, evidence, authoritative)
    problems = 0
    for doc_id, kind, data in documents:
        for label, text, ref in read_pairs(data, doc_id):
            field = LABELS.get(label)
            if not field:
                continue
            authoritative = AUTHORITY.get(field) == kind
            if field in values and values[field][2] and not authoritative:
                continue  # keep the authoritative document's value
            values[field] = (text, ref, authoritative)
    out: dict = {}
    evidence: dict[str, EvidenceRef] = {}
    for field, (raw, ref, _) in values.items():
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
            amount = normalise_amount(raw)
            if amount is None:
                problems += 1
                continue
            out[field] = amount
        else:
            out[field] = raw
        evidence[field] = ref
    confidence = max(0.0, 1.0 - 0.2 * problems)
    return Extraction(**out, evidence=evidence, confidence=confidence)


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
