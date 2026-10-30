"""The dense channels have an approximate index. The planner walks it for a tenant with thousands of rows and answers a
handful of rows by the primary key; both paths are correct, and a width the index does not cover takes the exact scan."""

import os
import random
from uuid import uuid4

import pytest

from fitwitness.storage.repository import HNSW_CHANNELS, Repository
from test_corpus_state import drawing


@pytest.fixture
def repo():
    r = Repository(os.getenv("FITWITNESS_DATABASE_URL", "postgresql://fwadmin@/postgres?host=/tmp&port=55439"))
    r.migrate()
    return r


def corpus(repo, count, dims, channel="text"):
    from fitwitness.contracts import TenantScope

    scope = TenantScope(tenant_id=f"vx-{uuid4().hex[:12]}", user_id="t")
    rng = random.Random(count)
    repo.seed_revisions(scope, [drawing(scope, i, rng) for i in range(count)])
    ids = repo.snapshot(scope).revision_ids
    for rid in ids:
        repo.save_vector(scope, rid, channel, [rng.random() for _ in range(dims)])
    return scope, ids, [rng.random() for _ in range(dims)]


def bulk(repo, count, dims, channel):
    """A tenant with ``count`` drawings and one random vector each, written in one statement as the scale script does."""
    from fitwitness.contracts import TenantScope

    scope = TenantScope(tenant_id=f"vb-{uuid4().hex[:12]}", user_id="t")
    rng = random.Random(count)
    for start in range(0, count, 2500):
        repo.seed_revisions(scope, [drawing(scope, i, rng) for i in range(start, min(count, start + 2500))])
    with repo.plain() as c:
        c.execute(
            "INSERT INTO fw_vectors (tenant_id,revision_id,channel,embedding,metadata) "
            "SELECT r.tenant_id, r.id, %s, x.v, '{}'::jsonb FROM fw_revisions r CROSS JOIN LATERAL "
            "(SELECT ('['||string_agg(random()::text, ',')||']')::vector AS v FROM generate_series(1, %s) WHERE r.id IS NOT NULL) x "
            "WHERE r.tenant_id=%s",
            (channel, dims, scope.tenant_id),
        )
        c.execute("ANALYZE fw_vectors")
    return scope, repo.snapshot(scope).revision_ids, [rng.random() for _ in range(dims)]


def exact(repo, scope, channel, vector, ids, k):
    with repo.connection(scope) as c:
        c.execute("SELECT set_config('enable_indexscan','off',true), set_config('enable_bitmapscan','off',true)")
        rows = c.execute(
            "SELECT revision_id FROM fw_vectors WHERE channel=%s AND revision_id=ANY(%s) ORDER BY embedding <=> %s::vector, revision_id LIMIT %s",
            (channel, ids, str(vector), k),
        ).fetchall()
    return [r["revision_id"] for r in rows]


def test_migrate_builds_one_index_per_dense_channel(repo):
    with repo.plain() as c:
        rows = c.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename='fw_vectors' AND indexname LIKE 'fw_vectors_hnsw_%'"
        ).fetchall()
    defs = {r["indexname"]: r["indexdef"] for r in rows}
    assert set(defs) == {f"fw_vectors_hnsw_{ch}" for ch in HNSW_CHANNELS}
    for channel, dims in HNSW_CHANNELS.items():
        d = defs[f"fw_vectors_hnsw_{channel}"]
        assert "USING hnsw" in d and f"vector({dims})" in d and f"channel = '{channel}'" in d and f"vector_dims(embedding) = {dims}" in d


@pytest.mark.parametrize("channel", ["text", "image"])
def test_a_tenant_of_a_thousand_and_more_is_searched_through_the_index(repo, channel, monkeypatch):
    scope, ids, probe = bulk(repo, 1500, HNSW_CHANNELS[channel], channel)
    plan = repo.explain_vector_search(scope, channel, probe, ids, 10)
    assert f"Index Scan using fw_vectors_hnsw_{channel}" in plan, plan
    monkeypatch.setenv("FITWITNESS_HNSW_EF_SEARCH", "1000")  # pgvector's ceiling for the graph walk
    hits = [r["revision_id"] for r in repo.vector_search(scope, channel, probe, ids, 10)]
    truth = exact(repo, scope, channel, probe, ids, 10)
    # Approximate: on random vectors the walk can miss a neighbour even at the ceiling (docs/RETRIEVAL.md has the recall by
    # ef_search), so the check is overlap, not equality. A sequential scan would give all ten.
    assert len(hits) == 10 and len(set(hits) & set(truth)) >= 8, (hits, truth)


def test_a_handful_of_rows_is_answered_without_the_index_and_agrees_with_the_exact_scan(repo):
    scope, ids, probe = corpus(repo, 60, 384)
    hits = [r["revision_id"] for r in repo.vector_search(scope, "text", probe, ids, 10)]
    assert hits == exact(repo, scope, "text", probe, ids, 10)


def test_the_search_asks_for_at_least_as_many_candidates_as_rows(repo, monkeypatch):
    # hnsw.ef_search caps what the index returns; 40 (pgvector's default) under a top_k of 50 would lose rows.
    monkeypatch.setenv("FITWITNESS_HNSW_EF_SEARCH", "8")
    scope, ids, probe = corpus(repo, 60, 384)
    assert len(repo.vector_search(scope, "text", probe, ids, 50)) == 50
    monkeypatch.setenv("FITWITNESS_HNSW_EF_SEARCH", "nonsense")
    assert len(repo.vector_search(scope, "text", probe, ids, 50)) == 50


def test_a_vector_of_another_width_is_stored_outside_the_index_and_found_by_the_exact_scan(repo):
    scope, ids, probe = corpus(repo, 12, 3)  # the tests elsewhere write 3-wide vectors into the text channel
    plan = repo.explain_vector_search(scope, "text", probe, ids, 5)
    assert "fw_vectors_hnsw" not in plan, plan  # the primary key or a sequential scan, never the 384-wide index
    hits = [r["revision_id"] for r in repo.vector_search(scope, "text", probe, ids, 5)]
    assert hits == exact(repo, scope, "text", probe, ids, 5)
    with repo.plain() as c:  # the 3-wide rows are not in the 384-wide index, so no insert had to cast them
        n = c.execute("SELECT count(*) FROM fw_vectors WHERE tenant_id=%s AND vector_dims(embedding)=3", (scope.tenant_id,)).fetchone()[
            "count"
        ]
    assert n == 12


def test_the_statement_refuses_a_channel_name_it_would_not_quote(repo):
    from fitwitness.storage.repository import _vector_search_sql

    with pytest.raises(ValueError):
        _vector_search_sql("text' OR '1'='1", 3)
    assert "channel='image_dino'" in _vector_search_sql("image_dino", 384)
