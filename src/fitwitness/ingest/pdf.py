"""Extract explicit vector annotations; raster-only/ambiguous values stay absent.

A page that is only a picture is not read as fact. Its title block is read by OCR (``title_block.py``)
and kept as an "uncertain" observation the verifier never relies on."""

from io import BytesIO
from hashlib import sha256
from uuid import uuid5, NAMESPACE_URL
import re
import pdfplumber
from fitwitness.contracts import DrawingRevision, Fact, SourceRef, Interval

LABELS = {
    "HOLE_SPACING": "hole_spacing",
    "WIDTH": "width",
    "THICKNESS": "thickness",
    "MATERIAL": "material",
    "KIND": "kind",
    "DRAWING": "drawing_number",
    "REVISION": "revision",
}


def _picture_facts(data: bytes, page_no: int, revision: DrawingRevision) -> list[Fact]:
    """A page with no text layer is a picture: read its title block, and keep what it says as an observation.

    The facts are "uncertain" on purpose: the verifier answers "insufficient evidence" for them and the
    reviewer sees where on the page the text was read. A misread grade never becomes a match."""
    import os

    if os.getenv("FITWITNESS_TITLE_BLOCK_OCR", "on").lower() in ("off", "0", "false"):
        return []
    try:
        from fitwitness.ingest import title_block

        readings = title_block.read_page(data, page_no - 1)
    except Exception:  # noqa: BLE001 - an unreadable picture is a page with nothing on it, never a failed upload
        return []
    return [
        Fact(
            id=str(uuid5(NAMESPACE_URL, f"{revision.id}:{page_no}:{r.field}:{r.bbox}:ocr")),
            field=r.field,
            value=r.value,
            source=SourceRef(revision_id=revision.id, source_hash=revision.source_hash, page=page_no, bbox=r.bbox),
            method="title_block_ocr",
            certainty="uncertain",
        )
        for r in readings
        if r.field in ("material", "drawing_number")
    ]


def extract_pdf(data: bytes, revision: DrawingRevision) -> list[Fact]:
    if len(data) > 20 * 1024 * 1024 or not data.startswith(b"%PDF-"):
        raise ValueError("unsupported or oversized PDF")
    if sha256(data).hexdigest() != revision.source_hash:
        raise ValueError("source hash mismatch")
    facts = []
    try:
        with pdfplumber.open(BytesIO(data)) as pdf:
            if len(pdf.pages) > 20:
                raise ValueError("PDF page limit exceeded")
            for n, page in enumerate(pdf.pages, 1):
                words = page.extract_words()
                if not words:
                    facts += _picture_facts(data, n, revision)
                rows = {}
                for word in words:
                    rows.setdefault(round(word["top"] / 3), []).append(word)
                for row in rows.values():
                    row.sort(key=lambda w: w["x0"])
                    i = 0
                    while i < len(row):
                        key = row[i]["text"].rstrip(":")
                        if key not in LABELS or not row[i]["text"].endswith(":"):
                            i += 1
                            continue
                        start = i
                        i += 1
                        values = []
                        while i < len(row) and not row[i]["text"].endswith(":"):
                            values.append(row[i]["text"])
                            i += 1
                        if not values:
                            continue
                        field = LABELS[key]
                        unit = None
                        certainty = "verified"
                        if field in ("hole_spacing", "width", "thickness"):
                            if len(values) != 2 or not re.fullmatch(
                                r"\d+(\.\d+)?", values[0]
                            ):
                                continue
                            value = Interval(low=values[0], high=values[0])
                            unit = values[1]
                            if unit not in ("mm", "cm", "m", "in"):
                                certainty = "uncertain"
                        else:
                            value = " ".join(values)
                        group = row[start:i]
                        bbox = (
                            min(w["x0"] for w in group) / page.width,
                            min(w["top"] for w in group) / page.height,
                            max(w["x1"] for w in group) / page.width,
                            max(w["bottom"] for w in group) / page.height,
                        )
                        ref = SourceRef(
                            revision_id=revision.id,
                            source_hash=revision.source_hash,
                            page=n,
                            bbox=bbox,
                        )
                        facts.append(
                            Fact(
                                id=str(
                                    uuid5(
                                        NAMESPACE_URL,
                                        f"{revision.id}:{n}:{field}:{bbox}",
                                    )
                                ),
                                field=field,
                                value=value,
                                unit=unit,
                                source=ref,
                                certainty=certainty,
                            )
                        )
    except (ValueError, TypeError):
        raise
    except Exception as exc:
        raise ValueError("unreadable PDF") from exc
    return facts
