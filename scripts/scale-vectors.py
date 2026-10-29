#!/usr/bin/env python3
"""The dense channels at 10,000 and 100,000 drawings: what a search through the text and image vectors costs.

    python scripts/scale-vectors.py --sizes 10000 100000 --output docs/evaluation/scale-vectors.json

scale-search.py measured the identifier and BM25 channels. This script adds the vector channels with the real search path
(``retrieval.pipeline.search`` with the semantic and image channels, ``Repository.vector_search``, and the index check
that runs before them), but with RANDOM vectors and a stand-in encoder: a text embedding of 384 numbers and an image
embedding of 512 per drawing, written by the database. The numbers are therefore latency and memory only. They say
nothing about retrieval quality, and the real encoders (E5 and OpenCLIP, which take tens of milliseconds per query on a
CPU) are not in them. The drawings carry no facts.

Since 2026-10-29 each channel has an HNSW index (storage/repository.py, HNSW_CHANNELS). The run drops the indexes, loads
the vectors, builds the indexes again (timed: the bulk path), then measures the search over the index and its recall
against the exact scan at several hnsw.ef_search values, and what inserting 1,000 more vectors costs with the indexes in
place (the incremental path the indexer pays). ``--no-ann`` leaves the indexes dropped and measures the exact scan, as
the record before 2026-10-29 did.
"""
import argparse
import gc
import json
import os
import random
import resource
import statistics
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import numpy as np

from fitwitness.contracts import DrawingRevision, SearchRequest, TenantScope
from fitwitness.evaluation.provenance import git_sha
from fitwitness.retrieval.pipeline import search
from fitwitness.storage.repository import HNSW_CHANNELS, Repository, hnsw_index_statements


class StandIn:
    """Encoders that answer with random vectors of the sizes the real ones produce."""

    fingerprint = "scale-stand-in"

    def __init__(self, seed):
        self.rng = np.random.default_rng(seed)

    def encode_text(self, texts, query=False):
        return self.rng.random((len(texts), 384), dtype=np.float32)

    def encode_image(self, images):
        return self.rng.random((len(images), 512), dtype=np.float32)


def timed(fn):
    started = time.perf_counter()
    out = fn()
    return out, round((time.perf_counter() - started) * 1000, 1)


def rss_mb():
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def seed(repo, scope, n):
    batch, ids = [], []
    for i in range(n):
        number = f"SC-{i // 5:05d}-{i % 5}"
        payload = f"stub pdf {scope.tenant_id} {number}".encode()
        digest = sha256(payload).hexdigest()
        r = DrawingRevision(tenant_id=scope.tenant_id, document_id=f"doc-{i}", drawing_number=number, family_id=f"SC-{i // 5:05d}",
                            revision_label="A", source_hash=digest, title=f"part {number}", kind="bracket")
        ids.append(r.id)
        batch.append((r, payload, [], {"png": b"\x89PNG stand-in"} if i == 0 else {}))
        if len(batch) == 5000:
            repo.seed_revisions(scope, batch)
            batch = []
    if batch:
        repo.seed_revisions(scope, batch)
    return ids


def add_vectors(repo, scope):
    with repo.plain() as c:
        c.execute(
            "INSERT INTO fw_vectors (tenant_id,revision_id,channel,embedding,metadata) "
            "SELECT r.tenant_id, r.id, ch.c, x.v, '{\"encoder_fingerprint\":\"scale-stand-in\"}'::jsonb FROM fw_revisions r "
            "CROSS JOIN (VALUES ('text',384),('image',512)) AS ch(c,d) "
            "CROSS JOIN LATERAL (SELECT ('['||string_agg(random()::text, ',')||']')::vector AS v FROM generate_series(1, ch.d) "
            "WHERE r.id IS NOT NULL) x WHERE r.tenant_id=%s", (scope.tenant_id,))


def drop_indexes(repo):
    with repo.plain() as c:
        for channel in HNSW_CHANNELS:
            c.execute(f"DROP INDEX IF EXISTS fw_vectors_hnsw_{channel}")


def build_indexes(repo):
    """The indexes migrate() creates, built here after the load with 1 GB of maintenance memory (the graph is built in
    memory when it fits; pgvector's default 64 MB spills a 100,000-row graph): the bulk path. Returns their sizes in MB."""
    with repo.plain() as c:
        c.execute("SELECT set_config('maintenance_work_mem', '1GB', true)")
        for statement in hnsw_index_statements():
            c.execute(statement)
        return {ch: round(c.execute("SELECT pg_relation_size(%s) AS b", (f"fw_vectors_hnsw_{ch}",)).fetchone()["b"] / 2**20, 1) for ch in HNSW_CHANNELS}


def exact_search(repo, scope, channel, vector, ids, k):
    """The statement the search used before the index (the record of 2026-10-29 morning): the primary key finds the
    tenant's rows and every distance is computed. It cannot use the HNSW index, so it is the exact answer and the
    "before" time in one."""
    with repo.connection(scope) as c:
        return [r["revision_id"] for r in c.execute(
            "SELECT revision_id FROM fw_vectors WHERE channel=%s AND revision_id=ANY(%s) ORDER BY embedding <=> %s::vector, revision_id LIMIT %s",
            (channel, ids, str(vector), k)).fetchall()]


def recall(repo, scope, ids, queries, k=50):
    """Index search against the exact scan, per channel and ef_search: how many of the exact top-k the index returned,
    how many rows it returned at all, and the latency of each."""
    out = {"exact": "the statement before the index (primary key path), the exact answer and the before time"}
    rng = np.random.default_rng(11)
    for channel, dims in [("text", 384), ("image", 512)]:
        probes = [rng.random(dims, dtype=np.float32).tolist() for _ in range(queries)]
        truth = [set(exact_search(repo, scope, channel, p, ids, k)) for p in probes]
        exact_ms = [timed(lambda p=p: exact_search(repo, scope, channel, p, ids, k))[1] for p in probes]
        out[channel] = {"exact_ms_median": statistics.median(exact_ms), "plan": next((line.strip() for line in repo.explain_vector_search(scope, channel, probes[0], ids, k).split("\n") if "Scan" in line), "")}
        for ef in (40, 100, 200, 400):
            os.environ["FITWITNESS_HNSW_EF_SEARCH"] = str(ef)
            got, ms = [], []
            for p in probes:
                rows, t = timed(lambda p=p: repo.vector_search(scope, channel, p, ids, k))
                got.append([r["revision_id"] for r in rows]); ms.append(t)
            out[channel][f"ef_{ef}"] = {"recall_at_50": round(statistics.mean(len(set(g) & t) / k for g, t in zip(got, truth)), 3),
                                        "rows_returned_min": min(len(g) for g in got), "ms_median": statistics.median(ms), "ms_max": max(ms)}
        os.environ.pop("FITWITNESS_HNSW_EF_SEARCH", None)
    return out


def insert_more(repo, scope, count=1000):
    """What the indexer pays per vector with the indexes in place: ``count`` more drawings and their two vectors."""
    from fitwitness.contracts import DrawingRevision
    batch = []
    for i in range(count):
        number = f"IN-{i:05d}-0"
        payload = f"stub more {scope.tenant_id} {number}".encode()
        r = DrawingRevision(tenant_id=scope.tenant_id, document_id=f"more-{i}", drawing_number=number, family_id=f"IN-{i:05d}",
                            revision_label="A", source_hash=sha256(payload).hexdigest(), title=f"part {number}", kind="bracket")
        batch.append((r, payload, [], {}))
    repo.seed_revisions(scope, batch)
    with repo.plain() as c:
        c.execute(
            "INSERT INTO fw_vectors (tenant_id,revision_id,channel,embedding,metadata) "
            "SELECT r.tenant_id, r.id, ch.c, x.v, '{\"encoder_fingerprint\":\"scale-stand-in\"}'::jsonb FROM fw_revisions r "
            "CROSS JOIN (VALUES ('text',384),('image',512)) AS ch(c,d) "
            "CROSS JOIN LATERAL (SELECT ('['||string_agg(random()::text, ',')||']')::vector AS v FROM generate_series(1, ch.d) "
            "WHERE r.id IS NOT NULL) x WHERE r.tenant_id=%s AND r.document_id LIKE 'more-%%'", (scope.tenant_id,))


def watch_database(dsn, seconds, samples):
    """What the other sessions are doing while one slow call runs: the wait event and the first words of each query."""
    import threading

    import psycopg

    def run():
        with psycopg.connect(dsn, autocommit=True) as c:
            for _ in range(int(seconds / 3)):
                time.sleep(3)
                rows = c.execute("SELECT state, wait_event_type, wait_event, left(query, 80) AS q FROM pg_stat_activity "
                                 "WHERE state <> 'idle' AND pid <> pg_backend_pid()").fetchall()
                samples.append([{"state": r[0], "wait": f"{r[1]}/{r[2]}", "query": r[3]} for r in rows])

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def measure(repo, n, queries, analyze, ann):
    scope = TenantScope(tenant_id=f"vec-{n}-{uuid4().hex[:8]}", user_id="scale")
    drop_indexes(repo)  # the load goes into a table without indexes, then they are built: the bulk path
    started = time.perf_counter()
    ids = seed(repo, scope, n)
    seed_ms = round((time.perf_counter() - started) * 1000, 1)
    _, vectors_ms = timed(lambda: add_vectors(repo, scope))
    analyze_ms = timed(repo.analyze_vectors)[1] if analyze else None  # what the bulk indexer does when it finishes
    index = {}
    if ann:
        sizes, build_ms = timed(lambda: build_indexes(repo))
        index = {"build_after_load_ms": build_ms, "size_mb": sizes}
        _, more_ms = timed(lambda: insert_more(repo, scope, 1000))
        index["insert_1000_more_drawings_with_index_ms"] = more_ms
    gc.collect()
    encoders = StandIn(7)
    snapshot, cold_ms = timed(lambda: repo.snapshot(scope))
    rss_cold = rss_mb()
    from fitwitness.retrieval.indexing import require_index
    _, index_check_ms = timed(lambda: require_index(scope, snapshot, repo, encoders))
    probe = encoders.encode_text(["q"], query=True)[0].tolist()
    samples: list = []
    watch_database(repo.dsn, 150, samples)  # sampled every 3 s for the first search only (the thread stops after 150 s)
    _, vector_ms = timed(lambda: repo.vector_search(scope, "text", probe, snapshot.revision_ids, 50))
    _, vector_again_ms = timed(lambda: repo.vector_search(scope, "text", probe, snapshot.revision_ids, 50))
    if ann:
        index["recall"] = recall(repo, scope, snapshot.revision_ids, queries)
    rng = random.Random(3)
    rows = {"semantic": [], "image": [], "all_channels": []}
    for _ in range(queries):
        number = f"SC-{rng.randrange(n // 5):05d}-{rng.randrange(5)}"
        text = SearchRequest(text=f"{number} part")
        rows["semantic"].append(timed(lambda: search(scope, text, snapshot, repo, encoders=encoders, channels={"semantic"}))[1])
        image = SearchRequest(text="", image_id=ids[0])
        rows["image"].append(timed(lambda: search(scope, image, snapshot, repo, encoders=encoders, channels={"image"}))[1])
        both = SearchRequest(text=f"{number} part", image_id=ids[0])
        rows["all_channels"].append(timed(lambda: search(scope, both, snapshot, repo, encoders=encoders,
                                                         channels={"exact", "bm25", "semantic", "image"}))[1])
    return {"drawings": n, "seed_ms": seed_ms, "vectors_written_ms": vectors_ms, "analyze_after_load_ms": analyze_ms, "index": index, "cold_snapshot_ms": cold_ms,
            "index_check_ms": index_check_ms, "first_vector_search_ms": vector_ms, "second_vector_search_ms": vector_again_ms,
            "database_during_first_search": samples[:8],
            "search_ms_median": {k: statistics.median(v) for k, v in rows.items()},
            "search_ms_max": {k: max(v) for k, v in rows.items()}, "peak_rss_mb_after_cold": rss_cold, "peak_rss_mb": rss_mb()}


def pgvector_version(repo):
    with repo.plain() as c:
        return c.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()["extversion"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", type=int, nargs="+", default=[10000, 100000])
    ap.add_argument("--queries", type=int, default=8)
    ap.add_argument("--no-analyze", action="store_true", help="do not refresh planner statistics after the load (the first search then runs without them)")
    ap.add_argument("--no-ann", action="store_true", help="measure the exact scan without the HNSW indexes (the record before 2026-10-29)")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    repo = Repository(os.environ["FITWITNESS_DATABASE_URL"])
    repo.migrate()
    results = []
    for n in args.sizes:
        r = measure(repo, n, args.queries, not args.no_analyze, not args.no_ann)
        print(json.dumps(r, ensure_ascii=False), flush=True)
        results.append(r)
    record = {"what": __doc__.split("\n\n")[0].strip(), "measured_at": datetime.now(timezone.utc).isoformat(),
              "code_sha": git_sha(dirty_suffix=True), "ann": "none (exact scan)" if args.no_ann else "HNSW per channel, pgvector " + pgvector_version(repo),
              "database": "local PostgreSQL 16 (pgserver), same machine; random vectors",
              "results": results}
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
