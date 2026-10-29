"""The kept corpus state must be what the database says, whatever wrote to it, and search over it must rank the way the
reading path does. The reading path (every revision and fact read, a BM25 index built per query) is the reference."""

import os
import random
from hashlib import sha256
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from fitwitness.contracts import DrawingRevision, Fact, Interval, SearchRequest, SourceRef, TenantScope
from fitwitness.retrieval.pipeline import search
from fitwitness.storage import corpus_state
from fitwitness.storage.repository import Repository

KINDS = {"bracket": "브래킷 지지대", "flange": "플랜지 커버", "shaft": "구동 샤프트", "housing": "베어링 하우징"}
MATERIALS = ["SUS304", "AL6061", "S45C", "SS400"]


@pytest.fixture
def repo():
    r = Repository(os.getenv("FITWITNESS_DATABASE_URL", "postgresql://fwadmin@/postgres?host=/tmp&port=55439"))
    r.migrate()
    return r


class Reading:
    """The repository without the kept state: search reads the whole corpus the way it always did."""

    def __init__(self, repo):
        self._repo = repo

    def __getattr__(self, name):
        if name == "corpus_state":
            raise AttributeError(name)
        return getattr(self._repo, name)


def drawing(scope, i, rng, **changes):
    kind = rng.choice(list(KINDS))
    number = f"TS-{i // 5:05d}-{i % 5}"
    payload = f"stub {scope.tenant_id} {number}".encode()
    digest = sha256(payload).hexdigest()
    fields = dict(tenant_id=scope.tenant_id, document_id=f"doc-{i}", drawing_number=number, family_id=f"TS-{i // 5:05d}",
                  revision_label="A", source_hash=digest, title=f"{KINDS[kind]} {number}", kind=kind)
    r = DrawingRevision(**{**fields, **changes})
    src = SourceRef(revision_id=r.id, source_hash=digest, page=1, bbox=(0.0, 0.0, 1.0, 1.0))
    facts = [Fact(field="material", value=rng.choice(MATERIALS), source=src),
             Fact(field="hole_spacing", value=str(rng.choice(range(20, 120, 2))), unit="mm", source=src),
             Fact(field="width", value=str(rng.choice(range(30, 200, 5))), unit="mm", source=src)]
    if i % 7 == 0:
        facts.append(Fact(field="thickness", value=Interval(low="3", high="5"), unit="mm", source=src))
    return r, payload, facts, {}


def seed(repo, count=40, seed_=11):
    scope = TenantScope(tenant_id=f"cs-{uuid4().hex[:12]}", user_id="t")
    rng = random.Random(seed_)
    assert repo.seed_revisions(scope, [drawing(scope, i, rng) for i in range(count)]) == count
    return scope, rng


def reference_digest(repo, scope):
    with repo.connection(scope) as c:
        snapshot, _, _ = repo.corpus_in(c)
    return snapshot


def test_the_snapshot_is_the_digest_it_always_was(repo):
    scope, rng = seed(repo)
    first = drawing(scope, 100, rng, document_id="doc-3")  # a competing leaf of doc-3: a conflict, so doc-3 leaves the snapshot
    repo.add_revision(scope, first[0], first[1])
    draft = drawing(scope, 101, rng, approval="draft")
    repo.add_revision(scope, draft[0], draft[1])
    repo.save_vector(scope, repo.snapshot(scope).revision_ids[0], "text", [0.1, 0.2, 0.3])
    kept = repo.snapshot(scope)
    reference = reference_digest(repo, scope)
    assert kept.id == reference.id and kept.revision_ids == reference.revision_ids
    assert first[0].id not in kept.revision_ids and draft[0].id not in kept.revision_ids  # a conflict and a draft are out of force


@pytest.mark.parametrize("write", ["add_revision", "facts", "vector", "delete_fact", "edit_revision", "withdraw"])
def test_every_kind_of_write_moves_the_kept_state(repo, write):
    scope, rng = seed(repo, 12)
    before = repo.corpus_state(scope)
    assert repo.corpus_state(scope) is before  # nothing written: the same state, not a rebuilt one
    target = before.ids[0]
    if write == "add_revision":
        r, payload, _, _ = drawing(scope, 200, rng)
        repo.add_revision(scope, r, payload)
    elif write == "facts":
        src = SourceRef(revision_id=target, source_hash=repo.get_revision(scope, target).source_hash, page=1, bbox=(0.0, 0.0, 1.0, 1.0))
        repo.save_facts(scope, target, [Fact(field="finish", value="anodized", source=src)])
    elif write == "vector":
        repo.save_vector(scope, target, "text", [0.5, 0.5, 0.5])
    elif write == "delete_fact":
        with repo.connection(scope) as c:
            c.execute("DELETE FROM fw_facts WHERE id=(SELECT id FROM fw_facts ORDER BY id LIMIT 1)")
    elif write == "edit_revision":
        with repo.connection(scope) as c:
            c.execute("UPDATE fw_revisions SET data=jsonb_set(data,'{title}','\"renamed\"') WHERE id=%s", (target,))
    else:
        with repo.connection(scope) as c:
            c.execute("UPDATE fw_revisions SET data=jsonb_set(data,'{approval}','\"withdrawn\"') WHERE id=%s", (target,))
    after = repo.corpus_state(scope)
    assert after is not before
    assert after.snapshot.id == reference_digest(repo, scope).id
    assert after.snapshot.id != before.snapshot.id
    assert [number for number in after.numbers] == [r.drawing_number for r in repo.corpus(scope)[1]]


def test_a_write_that_rolls_back_leaves_nothing_kept(repo):
    scope, rng = seed(repo, 10)
    before = repo.corpus_state(scope)
    r, payload, _, _ = drawing(scope, 300, rng)
    with pytest.raises(RuntimeError):
        with repo.connection(scope) as c:
            c.execute("INSERT INTO fw_revisions VALUES (%s,%s,%s,%s,%s)",
                      (scope.tenant_id, r.id, r.document_id, None, Jsonb(r.model_dump(mode="json"))))
            inside = repo.state_in(c, scope)  # sees its own uncommitted row...
            assert r.id in inside.ids
            raise RuntimeError("roll back")
    # ...and that state was not kept: the committed corpus is what it was.
    assert repo.corpus_state(scope).snapshot.id == before.snapshot.id == reference_digest(repo, scope).id
    # The next committed write reaches the version the rolled-back one had; nothing built under it may be reused.
    r2, payload2, _, _ = drawing(scope, 301, rng)
    repo.add_revision(scope, r2, payload2)
    assert r.id not in repo.corpus_state(scope).ids and r2.id in repo.corpus_state(scope).ids


def test_another_tenants_writes_do_not_invalidate_this_one(repo):
    mine, _ = seed(repo, 8)
    other, rng = seed(repo, 8)
    kept = repo.corpus_state(mine)
    r, payload, _, _ = drawing(other, 400, rng)
    repo.add_revision(other, r, payload)
    assert repo.corpus_state(mine) is kept
    assert repo.corpus_state(other).snapshot.id != kept.snapshot.id


def test_a_database_rebuilt_under_the_same_name_does_not_serve_the_old_state(repo):
    scope, _ = seed(repo, 8)
    kept = repo.corpus_state(scope)
    with repo.plain() as c:  # what a test teardown or a restore does: the counter row goes away and comes back new
        c.execute("DELETE FROM fw_corpus_version WHERE tenant_id=%s", (scope.tenant_id,))
    assert repo.corpus_state(scope) is not kept
    assert repo.corpus_state(scope).snapshot.id == kept.snapshot.id  # same rows, so the same digest


QUERIES = [
    "TS-00001-2 도면", "ts 00001 2 도면 찾아줘", "TS-OOOO1-2", "TS-00001-7", "TS-00009-9", "TS-00002 시리즈", "FW-00001-2",
    "브래킷 구멍 간격 40mm SUS304", "구동 샤프트", "플랜지를 찾아줘", "하우징 두께 4mm 이상", "AL6061 너비 100mm",
    "kind:bracket shaft flange", "zzzz", "TS-00003-1 SUS304 브래킷",
]


@pytest.mark.parametrize("ranking", ["rrf", "constraints"])
@pytest.mark.parametrize("id_matching", ["normalized", "token"])
def test_search_over_the_kept_state_ranks_as_the_reading_path_does(repo, ranking, id_matching, monkeypatch):
    from fitwitness.retrieval import pipeline

    used = []
    real = pipeline._search_indexed
    monkeypatch.setattr(pipeline, "_search_indexed", lambda *a, **k: used.append(1) or real(*a, **k))
    scope, _ = seed(repo, 60)
    snapshot = repo.snapshot(scope)
    for text in QUERIES:
        request = SearchRequest(text=text, ranking=ranking, top_k=10)
        kept = search(scope, request, snapshot, repo, id_matching=id_matching, return_pool=True)
        reading = search(scope, request, snapshot, Reading(repo), id_matching=id_matching, return_pool=True)
        assert [c.model_dump(mode="json") for c in kept] == [c.model_dump(mode="json") for c in reading], text
    assert len(used) == len(QUERIES)  # only the first of each pair went through the kept state


def test_search_refuses_a_stale_snapshot_and_one_that_changed_underneath(repo):
    scope, rng = seed(repo, 10)
    old = repo.snapshot(scope)
    r, payload, _, _ = drawing(scope, 500, rng)
    repo.add_revision(scope, r, payload)
    with pytest.raises(ValueError, match="stale"):
        search(scope, SearchRequest(text="TS-00000-0"), old, repo)


def test_the_remembered_states_stay_within_their_bounds(repo, monkeypatch):
    monkeypatch.setattr(corpus_state, "MAX_CACHED_STATES", 3)
    scopes = [seed(repo, 5)[0] for _ in range(5)]
    for s in scopes:
        repo.corpus_state(s)
    mine = [k for k in corpus_state._STATES if k[0] == repo.dsn]
    assert len(corpus_state._STATES) <= 3 and len(mine) <= 3
