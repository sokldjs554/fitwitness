"""Extract explicit vector annotations; raster-only/ambiguous values stay absent."""

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
