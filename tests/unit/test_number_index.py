"""The parsed-number index must answer exactly as scanning the list of numbers does."""

import random

from fitwitness.retrieval.drawing_number import DrawingReference, NumberIndex, _one_edit, match_reference, match_text, split_number


def corpus(rng, size):
    out = []
    for _ in range(size):
        prefix = rng.choice(["FW", "SC", "LJT", "AB"])
        groups = [f"{rng.randrange(10 ** n):0{n}d}" for n in rng.choice([(3, 1), (5, 1), (2, 2, 1), (4,)])]
        out.append("-".join([prefix, *groups]))
    return out


def references(rng, numbers):
    """What a typed number looks like: a corpus number as written, one edit away in any of the ways, or unrelated."""
    refs = []
    for dn in rng.sample(numbers, min(60, len(numbers))):
        prefix, groups = split_number(dn)
        digits = list("".join(groups))
        edits = [digits[:]]
        i = rng.randrange(len(digits))
        sub = digits[:]; sub[i] = rng.choice("0123456789"); edits.append(sub)
        edits.append(digits[:i] + digits[i + 1:])
        edits.append(digits[:i] + [rng.choice("0123456789")] + digits[i:])
        if len(digits) > 1:
            j = rng.randrange(len(digits) - 1)
            swap = digits[:]; swap[j], swap[j + 1] = swap[j + 1], swap[j]; edits.append(swap)
        for edit in edits:
            if not edit:
                continue
            shape = [len(g) for g in groups]
            cut, parts = 0, []
            for n in shape:  # regroup the edited digits like the original, the last group takes the remainder
                parts.append("".join(edit[cut:cut + n])); cut += n
            parts[-1] += "".join(edit[cut:])
            kept = tuple(p for p in parts if p)
            if kept:
                refs.append(DrawingReference(prefix, kept, "-".join([prefix, *kept]), 1.0, rng.random() < 0.2))
    refs.append(DrawingReference("ZZ", ("001", "2"), "ZZ-001-2", 1.0, False))
    return refs


def test_the_index_answers_as_a_scan_of_the_numbers_does():
    rng = random.Random(7)
    numbers = corpus(rng, 400)
    index = NumberIndex(numbers)
    refs = references(rng, numbers)
    assert len(refs) > 200
    for fuzzy in (True, False):
        for ref in refs:
            assert match_reference(ref, index, fuzzy=fuzzy) == match_reference(ref, numbers, fuzzy=fuzzy), (ref, fuzzy)


def test_a_typo_is_found_without_measuring_every_number():
    numbers = [f"SC-{i // 5:05d}-{i % 5}" for i in range(5000)]
    index = NumberIndex(numbers)
    ref = DrawingReference("SC", ("00100", "9"), "SC-00100-9", 1.0, False)  # no such member: one edit from SC-00100-0..4 (and others)
    assert [m[0] for m in match_reference(ref, index)] == [m[0] for m in match_reference(ref, numbers)] != []
    # the neighbourhood is a few hundred strings, however large the corpus is
    assert len(list(_one_edit("001009"))) < 200


def test_text_matching_takes_a_list_or_an_index():
    numbers = ["FW-000-4", "FW-001-2", "LJT-12-3"]
    for text in ("fw 000 4 도면", "FW-OOO-4", "FW-001 시리즈", "FW-001-3", "nothing here"):
        assert match_text(text, numbers) == match_text(text, NumberIndex(numbers))
