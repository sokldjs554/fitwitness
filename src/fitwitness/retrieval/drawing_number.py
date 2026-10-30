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
# Hyphens of every kind (hyphen, non-breaking, figure, en and em dash, horizontal bar, minus) and the dot are written for the same hyphen.
_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-")
_SEP = r"[\s_.\-]"
_REF_RE = re.compile(
    rf"(?<![A-Za-z0-9])(?P<prefix>[A-Za-z]{{2,4}}){_SEP}*(?P<groups>[0-9OoIlSB]{{1,5}}(?:{_SEP}+[0-9OoIlSB]{{1,5}})*)"
    rf"(?P<rev>(?:[\s_-]*rev\.?[\s_-]*)?[A-Za-z])?(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(rf"{_SEP}+")
REVISION_CONFIDENCE = 0.9  # the number is the same drawing, asked for at a revision the index does not distinguish
PADDED_CONFIDENCE = 0.9    # the same number written with other zero padding or grouping

MatchMode = Literal["exact", "fuzzy", "series"]


@dataclass(frozen=True)
class DrawingReference:
    prefix: str
    groups: tuple[str, ...]
    raw: str
    confidence: float
    series: bool
    revision: str | None = None

    @property
    def canonical(self) -> str:
        return canonical(self.prefix, self.groups)

    @property
    def digits(self) -> str:
        return "".join(self.groups)


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_DASHES)


def canonical(prefix: str, groups: Iterable[str]) -> str:
    return "-".join([prefix.upper(), *groups])


def split_number(drawing_number: str) -> tuple[str, tuple[str, ...]] | None:
    """``'FW-000-4'`` -> ``('FW', ('000', '4'))``; None when the string has no digit group."""
    parts = _SPLIT_RE.split(_normalise(drawing_number).strip())
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
    text = _normalise(text)
    series_hint = bool(_SERIES_RE.search(text))
    refs: list[DrawingReference] = []
    for m in _REF_RE.finditer(text):
        prefix = m.group("prefix").upper()
        if prefix not in known:
            continue
        raw_groups = _SPLIT_RE.split(m.group("groups"))
        revision = m.group("rev")
        revision = re.sub(r"^[\s_-]*(rev\.?)?[\s_-]*", "", revision, flags=re.IGNORECASE).upper() if revision else None
        # B and S read as digits inside a group, so a second prefix written after a number ("FW-000-4 BS-120") would be
        # swallowed as the digits 85; a letters-only token that is a corpus prefix starts the next reference instead.
        segments, start = [], 0
        for i in range(1, len(raw_groups) - 1):
            if raw_groups[i].isalpha() and raw_groups[i].upper() in known:
                segments.append((prefix, raw_groups[start:i]))
                prefix, start = raw_groups[i].upper(), i + 1
        segments.append((prefix, raw_groups[start:]))
        for k, (seg_prefix, seg_groups) in enumerate(segments):
            corrected = [g.translate(_CONFUSABLE) for g in seg_groups]
            if not all(g.isdigit() for g in corrected):
                continue
            # One confusable correction per digit group is one mistake (``OOO`` is clearly ``000``).
            changed_groups = sum(a != b for a, b in zip(seg_groups, corrected))
            confidence = 1.0 if changed_groups == 0 else max(0.5, 1.0 - 0.2 * changed_groups)
            seg_revision = revision if k == len(segments) - 1 else None
            if seg_revision:
                confidence = round(confidence * REVISION_CONFIDENCE, 4)
            raw = m.group(0).strip() if len(segments) == 1 else f"{seg_prefix} {' '.join(seg_groups)}"
            refs.append(DrawingReference(seg_prefix, tuple(corrected), raw, confidence, series_hint, seg_revision))
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


class NumberIndex:
    """A corpus's drawing numbers split once, so a query does not parse every number again.

    ``match_text`` and ``match_reference`` take one in place of the list of numbers and return the same hits. The
    parsed numbers are grouped by prefix; the one-edit typo match looks up the strings one edit away from the typed
    digits instead of measuring the distance to every number with that prefix."""

    def __init__(self, drawing_numbers: Iterable[str]):
        self.by_prefix: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
        self._by_digits: dict[str, dict[str, list[tuple[str, tuple[str, ...]]]]] = {}
        self._ascii = True
        for dn in drawing_numbers:
            parts = split_number(dn)
            if parts:
                self.by_prefix.setdefault(parts[0], []).append((dn, parts[1]))
                self._ascii = self._ascii and "".join(parts[1]).isascii()

    @property
    def prefixes(self) -> set[str]:
        return set(self.by_prefix)

    def near_digits(self, prefix: str, digits: str) -> list[tuple[str, tuple[str, ...]]] | None:
        """Numbers of this prefix whose joined digits are within one edit of ``digits`` (a superset of the numbers at
        distance one: the caller still measures each). None when the digits fall outside what the neighbourhood
        covers (non-ASCII digits), and the caller scans instead."""
        if not self._ascii or not digits.isascii():
            return None
        table = self._by_digits.get(prefix)
        if table is None:
            table = self._by_digits[prefix] = {}
            for dn, groups in self.by_prefix.get(prefix, []):
                table.setdefault("".join(groups), []).append((dn, groups))
        found, seen = [], set()
        for candidate in _one_edit(digits):
            if candidate not in seen:
                seen.add(candidate)
                found.extend(table.get(candidate, ()))
        return found


def _one_edit(digits: str):
    """``digits`` and every string one substitution, deletion, insertion or adjacent swap away (digits 0-9)."""
    yield digits
    for i in range(len(digits)):
        yield digits[:i] + digits[i + 1:]
        for d in "0123456789":
            if d != digits[i]:
                yield digits[:i] + d + digits[i + 1:]
        if i + 1 < len(digits) and digits[i] != digits[i + 1]:
            yield digits[:i] + digits[i + 1] + digits[i] + digits[i + 2:]
    for i in range(len(digits) + 1):
        for d in "0123456789":
            yield digits[:i] + d + digits[i:]


def match_reference(
    ref: DrawingReference, drawing_numbers: Iterable[str] | NumberIndex, *, fuzzy: bool = True
) -> list[tuple[str, float, MatchMode]]:
    """Rank corpus drawing numbers against one reference.

    Returns ``(drawing_number, score, mode)`` sorted by score. Exact matches score the
    reference confidence; a series reference (``FW-000 시리즈`` or a reference with fewer
    groups than the corpus numbers) scores every member 0.8; one-edit typos score 0.7.
    Fuzzy candidates are only offered when nothing matched exactly, so a correct
    identifier is never diluted by its neighbours."""
    if isinstance(drawing_numbers, NumberIndex):
        index = drawing_numbers
        same_prefix = index.by_prefix.get(ref.prefix, [])
    else:
        index = None
        parsed = [(dn, split_number(dn)) for dn in drawing_numbers]
        same_prefix = [(dn, groups) for dn, parts in parsed if parts and parts[0] == ref.prefix for groups in [parts[1]]]
    exact = [(dn, ref.confidence, "exact") for dn, groups in same_prefix if groups == ref.groups]
    if exact:
        return sorted(exact, key=lambda x: (-x[1], x[0]))
    if not ref.series:
        # The same number with other zero padding ("FW-0-4", "FW-00-04") or grouping ("FW-0004") is that number, not a typo of it.
        as_ints = tuple(int(g) for g in ref.groups)
        padded = [(dn, PADDED_CONFIDENCE * ref.confidence, "exact") for dn, groups in same_prefix
                  if (len(groups) == len(ref.groups) and tuple(int(g) for g in groups) == as_ints)
                  or (len(groups) != len(ref.groups) and "".join(groups) == ref.digits)]
        if padded:
            return sorted(padded, key=lambda x: (-x[1], x[0]))
    out: list[tuple[str, float, MatchMode]] = []
    partial = ref.series or any(len(groups) > len(ref.groups) for _, groups in same_prefix)
    if partial:
        out.extend((dn, 0.8 * ref.confidence, "series") for dn, groups in same_prefix if groups[: len(ref.groups)] == ref.groups)
    if not out and fuzzy:
        near = index.near_digits(ref.prefix, ref.digits) if index is not None else None
        for dn, groups in (same_prefix if near is None else near):
            if len(groups) != len(ref.groups):
                continue
            distance = damerau_levenshtein("".join(groups), ref.digits, cap=1)
            if distance <= 1:
                out.append((dn, (0.7 - 0.1 * (distance - 1)) * ref.confidence, "fuzzy"))
    return sorted(out, key=lambda x: (-x[1], x[0]))


def match_text(text: str, drawing_numbers: Iterable[str] | NumberIndex, *, fuzzy: bool = True) -> dict[str, tuple[float, MatchMode]]:
    """All identifier hits for a free-text query: ``{drawing_number: (score, mode)}``."""
    index = drawing_numbers if isinstance(drawing_numbers, NumberIndex) else NumberIndex(drawing_numbers)
    hits: dict[str, tuple[float, MatchMode]] = {}
    for ref in extract_references(text, index.prefixes):
        for dn, score, mode in match_reference(ref, index, fuzzy=fuzzy):
            if dn not in hits or hits[dn][0] < score:
                hits[dn] = (score, mode)
    return hits
