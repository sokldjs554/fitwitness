import os, importlib.util, hashlib, json
from pathlib import Path
from uuid import uuid4
import pytest
from fitwitness.contracts import DrawingRevision, TenantScope, RunRequest, SearchRequest
from fitwitness.storage.repository import Repository
from fitwitness.ingest.pdf import extract_pdf


@pytest.fixture
def env():
    assert importlib.util.find_spec("fitwitness.runtime.jobs"), "durable runs missing"
    from fitwitness.runtime.jobs import Jobs

    r = Repository(
        os.getenv(
            "FITWITNESS_DATABASE_URL",
            "postgresql://fwadmin@/postgres?host=/tmp&port=55439",
        )
    )
    r.migrate()
    j = Jobs(r)
    j.migrate()
    s = TenantScope(tenant_id=str(uuid4()), user_id=str(uuid4()))
    entries = json.loads(Path("var/corpus/manifest.json").read_text())[
        "document_entries"
    ][:5]
    for e in entries:
        data = Path("var/corpus", e["pdf"]).read_bytes()
        d = DrawingRevision(
            tenant_id=s.tenant_id,
            **{
                k: e[k]
                for k in [
                    "id",
                    "document_id",
                    "drawing_number",
                    "family_id",
                    "revision_label",
                    "supersedes",
                    "kind",
                    "title",
                    "source_hash",
                ]
            },
        )
        r.add_revision(s, d, data)
        r.save_facts(s, d.id, extract_pdf(data, d))
    return r, j, s


def request():
    return RunRequest(search=SearchRequest(text="브래킷 구멍 간격 40mm SUS304"))


def test_idempotency_reuses_identical_request(env):
    r, j, s = env
    a = j.enqueue(s, request(), "same")
    b = j.enqueue(s, request(), "same")
    assert a.id == b.id
    with pytest.raises(ValueError):
        j.enqueue(s, RunRequest(search=SearchRequest(text="different")), "same")


def test_graph_verifies_source_evidence(env):
    from fitwitness.agents.graph import execute_run

    r, j, s = env
    run = j.enqueue(s, request(), "test")
    execute_run(r, s, run.id)
    out = j.get(s, run.id)
    assert out.state == "completed"
    verdicts = [d.verdict for d in out.decisions]
    assert "match" in verdicts and "mismatch" in verdicts and "unknown" in verdicts
    assert all(
        e.source_refs
        for d in out.decisions
        for e in d.evidence
        if e.verdict != "unknown"
    )
    assert j.events(s, run.id)[-1]["kind"] == "completed"


def test_cancelled_run_cannot_finalize(env):
    from fitwitness.agents.graph import execute_run

    r, j, s = env
    run = j.enqueue(s, request(), "cancel")
    j.cancel(s, run.id)
    execute_run(r, s, run.id)
    assert j.get(s, run.id).state == "cancelled"


def test_foreign_run_is_not_visible(env):
    r, j, s = env
    run = j.enqueue(s, request(), "foreign")
    other = TenantScope(tenant_id=str(uuid4()), user_id=str(uuid4()))
    assert j.get(other, run.id) is None and j.events(other, run.id) == []


def test_snapshot_race_prevents_old_finalization(env):
    r, j, s = env
    run = j.enqueue(s, request(), "race")
    token = j.claim(s, run.id)
    e = json.loads(Path("var/corpus/manifest.json").read_text())["document_entries"][5]
    d = DrawingRevision(
        tenant_id=s.tenant_id,
        **{
            k: e[k]
            for k in [
                "id",
                "document_id",
                "drawing_number",
                "family_id",
                "revision_label",
                "supersedes",
                "kind",
                "title",
                "source_hash",
            ]
        },
    )
    r.add_revision(s, d, Path("var/corpus", e["pdf"]).read_bytes())
    assert not j.finalize(s, run.id, run.snapshot_id, [], token)
    assert j.get(s, run.id).state == "stale"
