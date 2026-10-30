"""Drawing-number variants people actually type must resolve to the right identifier."""
from fitwitness.retrieval.drawing_number import (
    damerau_levenshtein,
    extract_references,
    match_reference,
    match_text,
    split_number,
)

CORPUS = ["FW-000-0", "FW-000-1", "FW-000-2", "FW-000-3", "FW-000-4", "FW-001-0", "FW-001-1", "FW-010-0"]


def test_split_number_handles_corpus_shape():
    assert split_number("FW-000-4") == ("FW", ("000", "4"))
    assert split_number("fw_000_4") == ("FW", ("000", "4"))
    assert split_number("SUS304") is None


def test_variants_resolve_to_the_same_canonical_reference():
    for text in ["FW-000-4", "fw 000 4", "FW_000_4", "FW-000-4의 치수", "fw-000-4를 찾아줘"]:
        refs = extract_references(text, ["FW"])
        assert [r.canonical for r in refs] == ["FW-000-4"], text
        assert refs[0].confidence == 1.0


def test_confusable_characters_are_corrected_with_lower_confidence():
    (ref,) = extract_references("FW-OOO-4", ["FW"])
    assert ref.canonical == "FW-000-4" and ref.confidence < 1.0
    assert match_reference(ref, CORPUS)[0] == ("FW-000-4", ref.confidence, "exact")


def test_material_codes_and_unknown_prefixes_are_not_references():
    assert extract_references("브래킷 SUS304 AL6061", ["FW"]) == []
    assert extract_references("ZZ-000-4", ["FW"]) == []


def test_series_reference_returns_every_family_member():
    (ref,) = extract_references("FW-000 시리즈", ["FW"])
    hits = match_reference(ref, CORPUS)
    assert {dn for dn, _, mode in hits} == {f"FW-000-{i}" for i in range(5)} and {m for _, _, m in hits} == {"series"}
    # a reference with fewer groups than the corpus numbers is a series even without the word
    (short,) = extract_references("FW-001", ["FW"])
    assert {dn for dn, _, _ in match_reference(short, CORPUS)} == {"FW-001-0", "FW-001-1"}


def test_one_edit_typo_falls_back_to_fuzzy_only_when_nothing_matches_exactly():
    (ref,) = extract_references("FW-000-7", ["FW"])      # no such variant
    hits = match_reference(ref, CORPUS)
    assert hits and all(mode == "fuzzy" for _, _, mode in hits)
    assert {dn for dn, _, _ in hits} == {f"FW-000-{i}" for i in range(5)}
    (exact,) = extract_references("FW-000-1", ["FW"])
    assert match_reference(exact, CORPUS) == [("FW-000-1", 1.0, "exact")]   # neighbours are not offered
    assert match_reference(ref, CORPUS, fuzzy=False) == []


def test_transposition_is_one_edit():
    assert damerau_levenshtein("0001", "0010") == 1
    assert damerau_levenshtein("0001", "0001") == 0
    assert damerau_levenshtein("0001", "1100") > 1


def test_match_text_collects_all_references_in_a_sentence():
    hits = match_text("FW-000-1 과 fw 001 0 비교", CORPUS)
    assert set(hits) == {"FW-000-1", "FW-001-0"} and all(score == 1.0 for score, _ in hits.values())
    assert match_text("브래킷 너비 65mm", CORPUS) == {}


def test_a_second_prefix_after_a_number_is_a_new_reference_not_more_digit_groups():
    """B and S are read as 8 and 5 inside a group, so "BS" after "FW-000-4" used to become the digits 85 and the
    sentence turned into one number, ('000', '4', '85', '120')."""
    corpus = CORPUS + ["BS-120", "BS-121"]
    refs = extract_references("FW-000-4 BS-120", ["FW", "BS"])
    assert [r.canonical for r in refs] == ["FW-000-4", "BS-120"] and all(r.confidence == 1.0 for r in refs)
    assert match_text("FW-000-4 BS-120", corpus) == {"FW-000-4": (1.0, "exact"), "BS-120": (1.0, "exact")}
    assert [r.canonical for r in extract_references("BS-120", ["FW", "BS"])] == ["BS-120"]
    (letters,) = extract_references("FW-OOO-4", ["FW", "BS"])  # a group of letters that is no prefix is still a mistyped number
    assert letters.canonical == "FW-000-4" and letters.confidence < 1.0


def test_other_dashes_and_dots_separate_the_groups_like_the_hyphen():
    for text in ["FW–000–4", "FW—000—4", "FW‐000‐4", "FW−000−4", "FW.000.4", "FW.000-4", "fw 000.4 확인"]:
        assert [r.canonical for r in extract_references(text, ["FW"])] == ["FW-000-4"], text
    assert match_text("FW.000.4 를 찾아줘", CORPUS) == {"FW-000-4": (1.0, "exact")}
    assert match_text("도번 FW-000-4. 다음 문장", CORPUS) == {"FW-000-4": (1.0, "exact")}
    assert split_number("FW.000.4") == ("FW", ("000", "4")) and split_number("FW–000–4") == ("FW", ("000", "4"))


def test_zero_padding_and_regrouping_find_the_same_number_without_calling_it_a_typo():
    for text in ["FW-0004", "FW-0-4", "FW-00-04", "FW 000 04"]:
        (ref,) = extract_references(text, ["FW"])
        assert match_reference(ref, CORPUS) == [("FW-000-4", 0.9, "exact")], text
    (series,) = extract_references("FW-000", ["FW"])
    assert {m for _, _, m in match_reference(series, CORPUS)} == {"series"}  # a shorter reference is still a family
    (other,) = extract_references("FW-0005", ["FW"])
    assert {m for _, _, m in match_reference(other, CORPUS)} <= {"fuzzy"}  # a different number is not a padding of this one


def test_a_revision_letter_after_the_number_is_not_a_truncation_to_the_family():
    """FW-000-4A used to be cut to ('000',) with full confidence and answered as the whole FW-000 family."""
    (ref,) = extract_references("FW-000-4A", ["FW"])
    assert ref.canonical == "FW-000-4" and ref.revision == "A" and ref.confidence == 0.9
    assert match_text("FW-000-4A 도면", CORPUS) == {"FW-000-4": (0.9, "exact")}
    assert match_text("fw 000 4 rev b", CORPUS) == {"FW-000-4": (0.9, "exact")}
    assert match_text("FW-000-4의 치수", CORPUS) == {"FW-000-4": (1.0, "exact")}  # a particle is not a revision
