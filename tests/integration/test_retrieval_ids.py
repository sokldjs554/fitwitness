"""The lexical channel must find a drawing from the forms people actually type."""
from test_workflow import env
from fitwitness.contracts import SearchRequest
from fitwitness.retrieval.pipeline import search


def _first(r, s, text, **kw):
    hits = search(s, SearchRequest(text=text, top_k=5), r.snapshot(s), r, **kw)
    return hits[0] if hits else None


def test_identifier_variants_rank_the_referenced_drawing_first(env):
    r, _, s = env
    target = next(x for x in r.list_revisions(s) if x.drawing_number == "FW-000-4")
    for text in ["FW-000-4", "fw 000 4", "FW_000_4", "FW-OOO-4", "FW-000-4의 구멍 간격", "fw-000-4 브래킷"]:
        first = _first(r, s, text)
        assert first is not None and first.revision_id == target.id, text
        assert first.scores["exact"] >= 0.75


def test_series_reference_returns_the_whole_family(env):
    r, _, s = env
    hits = search(s, SearchRequest(text="FW-000 시리즈", top_k=10), r.snapshot(s), r)
    numbers = {next(x.drawing_number for x in r.list_revisions(s) if x.id == c.revision_id) for c in hits}
    assert numbers == {f"FW-000-{i}" for i in range(5)}
    assert all(c.scores["id_mode"] == 0.5 for c in hits)


def test_one_digit_typo_is_recovered_by_fuzzy_matching_only_in_normalized_mode(env):
    r, _, s = env
    numbers = {x.drawing_number for x in r.list_revisions(s)}
    assert "FW-000-7" not in numbers
    hits = search(s, SearchRequest(text="FW-000-7", top_k=5), r.snapshot(s), r)
    assert hits and all(c.scores["id_mode"] == 0.25 for c in hits)
    legacy = search(s, SearchRequest(text="FW-000-7", top_k=5), r.snapshot(s), r, id_matching="token")
    assert all("exact" not in c.scores for c in legacy)


def test_material_code_is_not_mistaken_for_an_identifier(env):
    r, _, s = env
    hits = search(s, SearchRequest(text="SUS304 브래킷", top_k=5), r.snapshot(s), r)
    assert hits and all("exact" not in c.scores for c in hits)
