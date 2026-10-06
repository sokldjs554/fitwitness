"""Drawing-number understanding for the lexical channel.

Shop-floor people write the same drawing many ways: ``FW-000-4``, ``fw 000 4``,
``FW_000_4``, ``FW-OOO-4`` (letter O for zero), ``FW-000-4의`` (a particle glued
on), or ``FW-000 시리즈`` for every variant of a family. The old exact channel
only matched the casefolded token, so each of those forms returned nothing.

This module turns free text into canonical references with a confidence score
and ranks a corpus of drawing numbers against a reference in exact, fuzzy or
series mode. It never reads gold labels; it only compares identifiers.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Literal

# Characters OCR and keyboards confuse with digits. Correction lowers confidence.
_CONFUSABLE = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "S": "5", "B": "8"})
_SERIES_RE = re.compile(r"(시리즈|계열|패밀리|series|family|\*)", re.IGNORECASE)
_REF_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<prefix>[A-Za-z]{2,4})[\s_-]*(?P<groups>[0-9OoIlSB]{1,5}(?:[\s_-]+[0-9OoIlSB]{1,5})*)(?![A-Za-z0-9])"
)

MatchMode = Literal["exact", "fuzzy", "series"]


@dataclass(frozen=True)
class DrawingReference:
    prefix: str
    groups: tuple[str, ...]
    raw: str
    confidence: float
    series: bool

    @property
    def canonical(self) -> str:
        return canonical(self.prefix, self.groups)

    @property
    def digits(self) -> str:
        return "".join(self.groups)


def canonical(prefix: str, groups: Iterable[str]) -> str:
    return "-".join([prefix.upper(), *groups])


def split_number(drawing_number: str) -> tuple[str, tuple[str, ...]] | None:
    """``'FW-000-4'`` -> ``('FW', ('000', '4'))``; None when the string has no digit group."""
    parts = re.split(r"[\s_-]+", unicodedata.normalize("NFKC", drawing_number).strip())
    if len(parts) < 2 or not parts[0].isalpha():
        return None
    groups = tuple(parts[1:])
    if not all(g.isdigit() for g in groups):
        return None
    return parts[0].upper(), groups


def extract_references(text: str, known_prefixes: Iterable[str]) -> list[DrawingReference]:
    """Find drawing references whose prefix exists in the corpus.

    Material codes such as ``SUS304`` or ``AL6061`` look like references but never
    carry a corpus prefix, so they are skipped."""
    known = {p.upper() for p in known_prefixes}
    text = unicodedata.normalize("NFKC", text)
    series_hint = bool(_SERIES_RE.search(text))
    refs: list[DrawingReference] = []
    for m in _REF_RE.finditer(text):
        prefix = m.group("prefix").upper()
        if prefix not in known:
            continue
        raw_groups = re.split(r"[\s_-]+", m.group("groups"))
        corrected = [g.translate(_CONFUSABLE) for g in raw_groups]
        if not all(g.isdigit() for g in corrected):
            continue
        # One confusable correction per digit group is one mistake (``OOO`` is clearly ``000``).
        changed_groups = sum(a != b for a, b in zip(raw_groups, corrected))
        confidence = 1.0 if changed_groups == 0 else max(0.5, 1.0 - 0.2 * changed_groups)
        refs.append(DrawingReference(prefix, tuple(corrected), m.group(0).strip(), confidence, series_hint))
    return refs


def damerau_levenshtein(a: str, b: str, cap: int = 2) -> int:
    """Edit distance with adjacent transposition; returns cap+1 once the cap is exceeded."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    previous_row: list[int] | None = None
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            current[j] = min(row[j] + 1, current[j - 1] + 1, row[j - 1] + cost)
            if previous_row is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                current[j] = min(current[j], previous_row[j - 2] + 1)
        if min(current) > cap:
            return cap + 1
        previous_row, row = row, current
    return row[-1]


def match_reference(
    ref: DrawingReference, drawing_numbers: Iterable[str], *, fuzzy: bool = True
) -> list[tuple[str, float, MatchMode]]:
    """Rank corpus drawing numbers against one reference.

    Returns ``(drawing_number, score, mode)`` sorted by score. Exact matches score the
    reference confidence; a series reference (``FW-000 시리즈`` or a reference with fewer
    groups than the corpus numbers) scores every member 0.8; one-edit typos score 0.7.
    Fuzzy candidates are only offered when nothing matched exactly, so a correct
    identifier is never diluted by its neighbours."""
    parsed = [(dn, split_number(dn)) for dn in drawing_numbers]
    same_prefix = [(dn, groups) for dn, parts in parsed if parts and parts[0] == ref.prefix for groups in [parts[1]]]
    exact = [(dn, ref.confidence, "exact") for dn, groups in same_prefix if groups == ref.groups]
    if exact:
        return sorted(exact, key=lambda x: (-x[1], x[0]))
    out: list[tuple[str, float, MatchMode]] = []
    partial = ref.series or any(len(groups) > len(ref.groups) for _, groups in same_prefix)
    if partial:
        out.extend((dn, 0.8 * ref.confidence, "series") for dn, groups in same_prefix if groups[: len(ref.groups)] == ref.groups)
    if not out and fuzzy:
        for dn, groups in same_prefix:
            if len(groups) != len(ref.groups):
                continue
            distance = damerau_levenshtein("".join(groups), ref.digits, cap=1)
            if distance <= 1:
                out.append((dn, (0.7 - 0.1 * (distance - 1)) * ref.confidence, "fuzzy"))
    return sorted(out, key=lambda x: (-x[1], x[0]))


def match_text(text: str, drawing_numbers: Iterable[str], *, fuzzy: bool = True) -> dict[str, tuple[float, MatchMode]]:
    """All identifier hits for a free-text query: ``{drawing_number: (score, mode)}``."""
    numbers = list(drawing_numbers)
    prefixes = {parts[0] for dn in numbers if (parts := split_number(dn))}
    hits: dict[str, tuple[float, MatchMode]] = {}
    for ref in extract_references(text, prefixes):
        for dn, score, mode in match_reference(ref, numbers, fuzzy=fuzzy):
            if dn not in hits or hits[dn][0] < score:
                hits[dn] = (score, mode)
    return hits
