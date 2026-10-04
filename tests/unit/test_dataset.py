import importlib.util, json, hashlib
import pytest


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    assert importlib.util.find_spec("fitwitness.data.generate"), (
        "CAD dataset generator not implemented"
    )
    from fitwitness.data.generate import generate_dataset

    root = tmp_path_factory.mktemp("cad")
    return root, generate_dataset(root)


def test_manifest_has_30_families_180_drawings_240_queries(dataset):
    root, m = dataset
    assert len(m["document_entries"]) == 180
    assert len(m["query_entries"]) == 240
    assert len({d["family_id"] for d in m["document_entries"]}) == 30
    assert all(
        (root / d["pdf"]).exists() and (root / d["step"]).exists()
        for d in m["document_entries"]
    )


def test_family_splits_disjoint(dataset):
    _, m = dataset
    s = m["family_splits"]
    assert [len(s[k]) for k in ["train", "dev", "test"]] == [18, 6, 6]
    assert not set(s["train"]) & set(s["test"])
    assert not set(s["dev"]) & set(s["test"])


def test_hashes_match_assets(dataset):
    root, m = dataset
    for entry in m["document_entries"]:
        assert (
            hashlib.sha256((root / entry["pdf"]).read_bytes()).hexdigest()
            == entry["source_hash"]
        )


def test_gold_unavailable_to_ingest(dataset):
    root, m = dataset
    assert (root / "gold/labels.json").exists()
    assert not any(p.name.startswith("gold") for p in (root / "input").rglob("*"))
    assert all("facts" not in e for e in m["document_entries"])


def test_query_categories_have_distinct_inputs_and_gold(dataset):
    _, m = dataset
    first = m["query_entries"][:8]
    assert len({(q["text"], q.get("image_id")) for q in first}) >= 7
    assert (
        next(q for q in first if q["category"] == "no_answer")["expected_matches"] == []
    )
    assert next(q for q in first if q["category"] == "image")["image_id"]
    assert all("requirements" in q and "expected_verdicts" in q for q in first)
