#!/usr/bin/env python3
"""HNSW recall against the exact scan on vectors a REAL text encoder wrote.

    python scripts/hnsw-recall-real.py --sizes 10000 100000 --queries 50 --output docs/evaluation/hnsw-recall-real.json

docs/evaluation/scale-vectors-hnsw.json measured the index on random vectors, which are the worst case for HNSW (every
point equally far from every other), and recall@50 was 0.93 at 10,000 drawings and 0.57 at 100,000. That says little about
real vectors, which cluster. This script encodes the text of N synthetic drawings with the pinned multilingual-E5 encoder
(the text a drawing is indexed with: title aliases, drawing number, kind, and the width, thickness, hole spacing and material
facts) and encodes natural-language queries of the kind the product receives, then compares the HNSW answer with the exact
one at several hnsw.ef_search values.

What this does not say: the texts are templated (four kinds, a few dozen distinct values), so the vectors cluster more tightly
than a real shop's drawings would; only the text channel is measured (the image encoders would need images); the data is
synthetic and the database is a CI container. It says how the index behaves on vectors that have structure.

``--stub-encoder`` replaces the encoder with a seeded clustered stand-in, for a dry run of this script where the model files
are not available. Its numbers mean nothing.
"""

import argparse
import json
import os
import random
import statistics
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import numpy as np

from fitwitness.contracts import DrawingRevision, TenantScope
from fitwitness.data.generate import ALIASES, KINDS
from fitwitness.evaluation.provenance import git_sha
from fitwitness.storage.repository import HNSW_CHANNELS, Repository, hnsw_index_statements

MATERIALS = ["SUS304", "AL6061", "SS400", "S45C", None]


def text_for(i: int, rng: random.Random) -> tuple[str, str, str]:
    """(kind, drawing number, the text a drawing is indexed with), as indexing.document_text writes it."""
    kind = KINDS[rng.randrange(4)]
    number = f"SC-{i // 5:05d}-{i % 5}"
    facts = [f"width {rng.randrange(60, 220)} mm", f"thickness {rng.randrange(3, 12)} mm"]
    if kind in ("bracket", "flange"):
        facts.append(f"hole_spacing {rng.randrange(30, 160)} mm")
    material = rng.choice(MATERIALS)
    if material:
        facts.append(f"material {material}")
    return kind, number, " ".join([ALIASES[kind], number, kind] + facts)


def query_for(rng: random.Random) -> str:
    kind = KINDS[rng.randrange(4)]
    word = {"bracket": "브래킷", "flange": "플랜지", "shaft": "축", "housing": "하우징"}[kind]
    parts = [word, rng.choice(["구멍 간격", "외형 폭", "두께", ""]), f"{rng.randrange(30, 200)}mm", rng.choice(MATERIALS) or ""]
    return " ".join(p for p in parts if p)


class StubEncoder:
    """Seeded clustered vectors of the width the real text encoder produces: a kind decides the cluster, the rest is noise."""

    def __init__(self):
        rng = np.random.default_rng(5)
        self.centres = rng.normal(size=(len(KINDS) + 1, 384)).astype(np.float32)

    def encode_text(self, texts, query=False):
        out = np.zeros((len(texts), 384), dtype=np.float32)
        for i, text in enumerate(texts):
            k = next((j for j, kind in enumerate(KINDS) if kind in text or ALIASES[kind].split()[0] in text), len(KINDS))
            noise = np.random.default_rng(int(sha256(text.encode()).hexdigest()[:8], 16)).normal(scale=0.6, size=384)
            out[i] = self.centres[k] + noise.astype(np.float32)
        return out / np.linalg.norm(out, axis=1, keepdims=True)


def seed_revisions(repo, scope, texts):
    batch, ids = [], []
    for i, (kind, number, _text) in enumerate(texts):
        payload = f"stub pdf {scope.tenant_id} {number}".encode()
        r = DrawingRevision(
            tenant_id=scope.tenant_id,
            document_id=f"doc-{i}",
            drawing_number=number,
            family_id=f"SC-{i // 5:05d}",
            revision_label="A",
            source_hash=sha256(payload).hexdigest(),
            title=f"part {number}",
            kind=kind,
        )
        ids.append(r.id)
        batch.append((r, payload, [], {}))
        if len(batch) == 5000:
            repo.seed_revisions(scope, batch)
            batch = []
    if batch:
        repo.seed_revisions(scope, batch)
    return ids


def write_vectors(repo, scope, ids, vectors, fingerprint):
    meta = json.dumps({"encoder_fingerprint": fingerprint})
    with repo.plain() as c:
        for start in range(0, len(ids), 2000):
            rows = [(scope.tenant_id, ids[j], "text", str(vectors[j].tolist()), meta) for j in range(start, min(start + 2000, len(ids)))]
            with c.cursor() as cur:
                cur.executemany(
                    "INSERT INTO fw_vectors (tenant_id,revision_id,channel,embedding,metadata) VALUES (%s,%s,%s,%s::vector,%s::jsonb)", rows
                )


def exact(repo, scope, vector, ids, k):
    with repo.connection(scope) as c:
        return [
            r["revision_id"]
            for r in c.execute(
                "SELECT revision_id FROM fw_vectors WHERE channel='text' AND revision_id=ANY(%s) ORDER BY embedding <=> %s::vector, revision_id LIMIT %s",
                (ids, str(vector), k),
            ).fetchall()
        ]


def timed(fn):
    started = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - started) * 1000


def measure(repo, encoder, fingerprint, n, queries):
    scope = TenantScope(tenant_id=f"real-{n}-{uuid4().hex[:8]}", user_id="scale")
    with repo.plain() as c:
        # An earlier size of this script's own run would otherwise stay in the index and be filtered out of every answer.
        c.execute("DELETE FROM fw_vectors WHERE tenant_id LIKE 'real-%'")
        for channel in HNSW_CHANNELS:
            c.execute(f"DROP INDEX IF EXISTS fw_vectors_hnsw_{channel}")
    rng = random.Random(n)
    texts = [text_for(i, rng) for i in range(n)]
    ids = seed_revisions(repo, scope, texts)
    vectors, encode_s = [], 0.0
    started = time.perf_counter()
    for start in range(0, n, 256):
        vectors.append(encoder.encode_text([t[2] for t in texts[start : start + 256]]))
    encode_s = time.perf_counter() - started
    vectors = np.concatenate(vectors)
    write_vectors(repo, scope, ids, vectors, fingerprint)
    repo.analyze_vectors()
    with repo.plain() as c:
        c.execute("SELECT set_config('maintenance_work_mem', '1GB', true)")

        def build():
            for statement in hnsw_index_statements():
                c.execute(statement)
            c.execute("SELECT 1").fetchone()  # the connection pipelines its statements: this makes the builds finish inside the timing

        _, build_ms = timed(build)
    snapshot = repo.snapshot(scope)
    qrng = random.Random(1000 + n)
    qtexts = [query_for(qrng) for _ in range(queries)]
    probes = encoder.encode_text(qtexts, query=True)
    out = {
        "drawings": n,
        "encode_seconds": round(encode_s, 1),
        "distinct_texts": len({t[2] for t in texts}),
        "index_build_ms": round(build_ms, 1),
        "queries": queries,
    }
    truth = [set(exact(repo, scope, p.tolist(), snapshot.revision_ids, 50)) for p in probes]
    out["exact_ms_median"] = round(
        statistics.median(timed(lambda p=p: exact(repo, scope, p.tolist(), snapshot.revision_ids, 50))[1] for p in probes[:10]), 1
    )
    out["plan"] = next(
        (
            ln.strip()
            for ln in repo.explain_vector_search(scope, "text", probes[0].tolist(), snapshot.revision_ids, 50).split("\n")
            if "Scan" in ln
        ),
        "",
    )
    for ef in (40, 100, 200, 400):
        os.environ["FITWITNESS_HNSW_EF_SEARCH"] = str(ef)
        got, ms = [], []
        for p in probes:
            rows, t = timed(lambda p=p: repo.vector_search(scope, "text", p.tolist(), snapshot.revision_ids, 50))
            got.append([r["revision_id"] for r in rows])
            ms.append(t)
        recalls = [len(set(g) & tr) / 50 for g, tr in zip(got, truth)]
        worst = min(range(len(recalls)), key=recalls.__getitem__)
        out[f"ef_{ef}"] = {
            "recall_at_50": round(statistics.mean(recalls), 3),
            "recall_at_50_worst_query": round(min(recalls), 3),
            "worst_query_text": qtexts[worst],
            "queries_below_half": sum(1 for x in recalls if x < 0.5),
            "recall_at_10": round(
                statistics.mean(
                    len(set(g[:10]) & set(list(tr_order)[:10])) / 10
                    for g, tr_order in zip(got, [exact(repo, scope, p.tolist(), snapshot.revision_ids, 10) for p in probes])
                ),
                3,
            ),
            "rows_returned_min": min(len(g) for g in got),
            "ms_median": round(statistics.median(ms), 1),
            "ms_max": round(max(ms), 1),
        }
    os.environ.pop("FITWITNESS_HNSW_EF_SEARCH", None)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", type=int, nargs="+", default=[10000, 100000])
    ap.add_argument("--queries", type=int, default=50)
    ap.add_argument("--stub-encoder", action="store_true")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    repo = Repository(os.environ["FITWITNESS_DATABASE_URL"])
    repo.migrate()
    if args.stub_encoder:
        encoder, fingerprint = StubEncoder(), "stub-clustered"
    else:
        from fitwitness.retrieval.embeddings import Encoders

        encoder = Encoders()
        fingerprint = encoder.fingerprint
    print("== meta", json.dumps({"code_sha": git_sha(dirty_suffix=True), "encoder_fingerprint": fingerprint}), flush=True)
    results = []
    for n in args.sizes:
        result = measure(repo, encoder, fingerprint, n, args.queries)
        print("== size", json.dumps(result, ensure_ascii=False), flush=True)
        results.append(result)
    record = {
        "what": __doc__.split("\n\n")[0].strip(),
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "code_sha": git_sha(dirty_suffix=True),
        "encoder": "stub (a dry run)" if args.stub_encoder else "pinned multilingual-E5 text encoder, 384 dimensions",
        "encoder_fingerprint": fingerprint,
        "database": "PostgreSQL 16 with pgvector in a CI container",
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
