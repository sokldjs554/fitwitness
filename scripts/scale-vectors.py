#!/usr/bin/env python3
"""The dense channels at 10,000 and 100,000 drawings: what a search through the text and image vectors costs.

    python scripts/scale-vectors.py --sizes 10000 100000 --output docs/evaluation/scale-vectors.json

scale-search.py measured the identifier and BM25 channels. This script adds the vector channels with the real search path
(``retrieval.pipeline.search`` with the semantic and image channels, ``Repository.vector_search``, and the index check
that runs before them), but with RANDOM vectors and a stand-in encoder: a text embedding of 384 numbers and an image
embedding of 512 per drawing, written by the database. The numbers are therefore latency and memory only. They say
nothing about retrieval quality, and the real encoders (E5 and OpenCLIP, which take tens of milliseconds per query on a
CPU) are not in them. The drawings carry no facts. No ANN index exists: pgvector scans the rows (the exact search).
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
from fitwitness.storage.repository import Repository


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


def measure(repo, n, queries, analyze):
    scope = TenantScope(tenant_id=f"vec-{n}-{uuid4().hex[:8]}", user_id="scale")
    started = time.perf_counter()
    ids = seed(repo, scope, n)
    seed_ms = round((time.perf_counter() - started) * 1000, 1)
    _, vectors_ms = timed(lambda: add_vectors(repo, scope))
    analyze_ms = timed(repo.analyze_vectors)[1] if analyze else None  # what the bulk indexer does when it finishes
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
    return {"drawings": n, "seed_ms": seed_ms, "vectors_written_ms": vectors_ms, "analyze_after_load_ms": analyze_ms, "cold_snapshot_ms": cold_ms,
            "index_check_ms": index_check_ms, "first_vector_search_ms": vector_ms, "second_vector_search_ms": vector_again_ms,
            "database_during_first_search": samples[:8],
            "search_ms_median": {k: statistics.median(v) for k, v in rows.items()},
            "search_ms_max": {k: max(v) for k, v in rows.items()}, "peak_rss_mb_after_cold": rss_cold, "peak_rss_mb": rss_mb()}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", type=int, nargs="+", default=[10000, 100000])
    ap.add_argument("--queries", type=int, default=8)
    ap.add_argument("--no-analyze", action="store_true", help="do not refresh planner statistics after the load (the first search then runs without them)")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    repo = Repository(os.environ["FITWITNESS_DATABASE_URL"])
    repo.migrate()
    results = []
    for n in args.sizes:
        r = measure(repo, n, args.queries, not args.no_analyze)
        print(json.dumps(r, ensure_ascii=False), flush=True)
        results.append(r)
    record = {"what": __doc__.split("\n\n")[0].strip(), "measured_at": datetime.now(timezone.utc).isoformat(),
              "code_sha": git_sha(dirty_suffix=True), "database": "local PostgreSQL 16 (pgserver), same machine; random vectors",
              "results": results}
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
