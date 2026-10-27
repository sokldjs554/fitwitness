#!/usr/bin/env python3
"""How drawing search holds up as the archive grows: synthetic drawings, the real search path, a local database.

    python scripts/scale-search.py --sizes 1000 10000 100000 --output docs/evaluation/scale-search.json

For each size a fresh workspace gets that many synthetic drawings through Repository.seed_revisions (a revision,
a stub PDF, five facts each; numbers like SC-01234-3, five drawings to a family). Then, with the same functions the
API calls, it times:

- ``snapshot``: Repository.snapshot, which the search endpoint calls before searching (it reads the whole corpus);
- ``search``: retrieval.pipeline.search with the default exact + BM25 channels, which reads the corpus again,
  builds a BM25 index over every drawing for the query, and reads the snapshot once more at the end;
- the parts inside it on the same corpus: the exact-ID matcher over every number and the BM25 build and score.

Queries per size: drawing numbers as written; lower-cased with the separators changed; with the zeros (or a 1)
of the family number typed as letters (O, I), which the ID matcher corrects; with the prefix's S typed as 5, which
it does not (it corrects letters inside digit groups only); and a free-text query. Above 10,000 drawings two
numbers are sampled instead of --queries, because each search reads the whole corpus three times. For the ID queries it checks whether
the first result is the drawing asked for. No model is called. The database is local (pgserver), so the network
time a hosted database adds to every corpus read is not in these numbers.
"""
import argparse
import gc
import json
import random
import resource
import statistics
import subprocess
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from rank_bm25 import BM25Okapi

from fitwitness.contracts import DrawingRevision, Fact, SearchRequest, SourceRef, TenantScope
from fitwitness.retrieval.drawing_number import match_text
from fitwitness.retrieval.pipeline import search, tokenize
from fitwitness.storage.repository import Repository

KINDS = {"bracket": "브래킷 지지대", "flange": "플랜지 커버", "shaft": "구동 샤프트", "housing": "베어링 하우징"}
MATERIALS = ["SUS304", "AL6061", "S45C", "SS400", "HT200"]


def drawings(scope, n, seed):
    rng = random.Random(seed)
    for i in range(n):
        kind = rng.choice(list(KINDS))
        number = f"SC-{i // 5:05d}-{i % 5}"
        payload = f"stub pdf {scope.tenant_id} {number}".encode()
        digest = sha256(payload).hexdigest()
        r = DrawingRevision(tenant_id=scope.tenant_id, document_id=f"doc-{i}", drawing_number=number,
                            family_id=f"SC-{i // 5:05d}", revision_label="A", source_hash=digest,
                            title=f"{KINDS[kind]} {number}", kind=kind)
        src = SourceRef(revision_id=r.id, source_hash=digest, page=1, bbox=(0.0, 0.0, 1.0, 1.0))
        facts = [Fact(field="kind", value=kind, source=src),
                 Fact(field="material", value=rng.choice(MATERIALS), source=src),
                 Fact(field="hole_spacing", value=str(rng.choice(range(20, 120, 2))), unit="mm", source=src),
                 Fact(field="width", value=str(rng.choice(range(30, 200, 5))), unit="mm", source=src),
                 Fact(field="thickness", value=str(rng.choice(range(2, 20))), unit="mm", source=src)]
        yield r, payload, facts, {}


def timed(fn):
    started = time.perf_counter()
    out = fn()
    return out, round((time.perf_counter() - started) * 1000, 1)


def rss_mb():
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def variants(number):
    prefix, family, member = number.split("-")
    lettered = family.replace("0", "O") if "0" in family else family.replace("1", "I", 1)
    return {"as_written": f"{number} 도면",
            "reformatted": f"{prefix.lower()} {family} {member} 도면 찾아줘",
            "digit_confusable": f"{prefix}-{lettered}-{member}",
            "prefix_confusable": f"{prefix.replace('S', '5')}-{family}-{member}"}


def measure(repo, n, queries, seed):
    scope = TenantScope(tenant_id=f"scale-{n}-{uuid4().hex[:8]}", user_id="scale")
    batch, seeded = [], 0
    started = time.perf_counter()
    for item in drawings(scope, n, seed):
        batch.append(item)
        if len(batch) == 5000:
            seeded += repo.seed_revisions(scope, batch)
            batch = []
    if batch:
        seeded += repo.seed_revisions(scope, batch)
    seed_ms = round((time.perf_counter() - started) * 1000, 1)
    assert seeded == n, (seeded, n)
    gc.collect()
    rng = random.Random(seed + 1)
    numbers = [f"SC-{i // 5:05d}-{i % 5}" for i in rng.sample(range(n), queries if n <= 10000 else 2)]
    ids = {r.drawing_number: r.id for r in repo.corpus(scope)[1]}
    rows, by_variant = [], {}
    for number in numbers:
        for name, text in variants(number).items():
            snapshot, snapshot_ms = timed(lambda: repo.snapshot(scope))
            hits, search_ms = timed(lambda: search(scope, SearchRequest(text=text), snapshot, repo))
            rows.append({"variant": name, "number": number, "snapshot_ms": snapshot_ms, "search_ms": search_ms,
                         "correct": bool(hits) and hits[0].revision_id == ids[number]})
            by_variant.setdefault(name, []).append(rows[-1])
    # The parts inside search, timed alone on the same corpus.
    current, revisions, facts = repo.corpus(scope)
    texts = [r.title + " " + r.drawing_number + " " + r.kind + " " + " ".join(f"{f.field} {f.value}" for f in facts[r.id]) for r in revisions]
    _, corpus_ms = timed(lambda: repo.corpus(scope))
    _, match_ms = timed(lambda: match_text(variants(numbers[0])["reformatted"], [r.drawing_number for r in revisions]))
    _, bm25_ms = timed(lambda: BM25Okapi([tokenize(t) or ["_"] for t in texts]).get_scores(tokenize("SUS304 브래킷 구멍 간격 40mm")))
    free, free_ms = timed(lambda: search(scope, SearchRequest(text="SUS304 브래킷 구멍 간격 40mm"), repo.snapshot(scope), repo))
    summary = {name: {"queries": len(v), "top1_correct": sum(r["correct"] for r in v),
                      "search_ms_median": statistics.median(r["search_ms"] for r in v),
                      "snapshot_ms_median": statistics.median(r["snapshot_ms"] for r in v)} for name, v in by_variant.items()}
    return {"drawings": n, "facts": n * 5, "seed_ms": seed_ms, "summary": summary,
            "parts_ms": {"corpus_read": corpus_ms, "exact_id_match": match_ms, "bm25_build_and_score": bm25_ms},
            "free_text": {"query": "SUS304 브래킷 구멍 간격 40mm", "search_ms": free_ms, "results": len(free)},
            "peak_rss_mb_after": rss_mb(), "rows": rows}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", type=int, nargs="+", default=[1000, 10000, 100000])
    ap.add_argument("--queries", type=int, default=10, help="sampled drawing numbers per size (three query forms each)")
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    import os
    repo = Repository(os.environ["FITWITNESS_DATABASE_URL"])
    repo.migrate()
    results = []
    for n in args.sizes:
        r = measure(repo, n, args.queries, args.seed)
        print(json.dumps({k: v for k, v in r.items() if k != "rows"}, ensure_ascii=False), flush=True)
        results.append(r)
    record = {"what": __doc__.split("\n\n")[0].strip(), "measured_at": datetime.now(timezone.utc).isoformat(),
              "code_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "database": "local PostgreSQL 16 (pgserver), same machine", "seed": args.seed, "results": results}
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
