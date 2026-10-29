#!/usr/bin/env python3
"""How drawing search holds up as the archive grows: synthetic drawings, the real search path, a local database.

    python scripts/scale-search.py --sizes 1000 10000 100000 --output docs/evaluation/scale-search.json

For each size a fresh workspace gets that many synthetic drawings through Repository.seed_revisions (a revision,
a stub PDF, five facts each; numbers like SC-01234-3, five drawings to a family). Then, with the same functions the
API calls, it times:

- ``cold``: the first Repository.snapshot after the import, which reads the corpus once and builds what search keeps
  (the snapshot id, the drawing numbers, the text of every drawing);
- ``snapshot``: Repository.snapshot afterwards, which asks the database whether the corpus changed and nothing else;
- ``search``: retrieval.pipeline.search with the default exact + BM25 channels over the kept state (the first query
  also builds the BM25 index and the number index once);
- with ``--reading``, the same search through the reading path (every revision and fact read, a BM25 index per query),
  which is what search was before the state was kept, for as many sizes as you give.

Queries per size: drawing numbers as written; lower-cased with the separators changed; with the zeros (or a 1)
of the family number typed as letters (O, I), which the ID matcher corrects; with the prefix's S typed as 5, which
it does not (it corrects letters inside digit groups only); a member that does not exist (one edit from five that do);
and a free-text query. For the ID queries it checks whether the first result is the drawing asked for (for the
missing member: a drawing of the same family). No model is called. The database is local (pgserver), so the network
time a hosted database adds to every read is not in these numbers.
"""
import argparse
import gc
import json
import random
import resource
import statistics
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from fitwitness.contracts import DrawingRevision, Fact, SearchRequest, SourceRef, TenantScope
from fitwitness.evaluation.provenance import git_sha
from fitwitness.retrieval.pipeline import search
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
            "prefix_confusable": f"{prefix.replace('S', '5')}-{family}-{member}",
            "missing_member": f"{prefix}-{family}-7"}


class Reading:
    """The repository without the kept state: search reads the whole corpus and indexes it per query, as it used to."""

    def __init__(self, repo):
        self._repo = repo

    def __getattr__(self, name):
        if name == "corpus_state":
            raise AttributeError(name)
        return getattr(self._repo, name)


FREE_TEXT = "SUS304 브래킷 구멍 간격 40mm"


def measure(repo, n, queries, seed, reading_up_to):
    scope = TenantScope(tenant_id=f"scale-{n}-{uuid4().hex[:8]}", user_id="scale")
    batch, seeded, ids = [], 0, {}
    started = time.perf_counter()
    for item in drawings(scope, n, seed):
        ids[item[0].drawing_number] = item[0].id
        batch.append(item)
        if len(batch) == 5000:
            seeded += repo.seed_revisions(scope, batch)
            batch = []
    if batch:
        seeded += repo.seed_revisions(scope, batch)
    seed_ms = round((time.perf_counter() - started) * 1000, 1)
    assert seeded == n, (seeded, n)
    del batch
    gc.collect()
    rss_seeded = rss_mb()
    # The first read after the import builds the kept state; later ones only ask whether the corpus changed.
    _, cold_ms = timed(lambda: repo.snapshot(scope))
    rss_cold = rss_mb()
    warm_ms = [timed(lambda: repo.snapshot(scope))[1] for _ in range(5)]
    snapshot = repo.snapshot(scope)
    # The first query also builds the number index and the BM25 index; they stay with the state.
    first, first_ms = timed(lambda: search(scope, SearchRequest(text=FREE_TEXT), snapshot, repo))
    rng = random.Random(seed + 1)
    numbers = [f"SC-{i // 5:05d}-{i % 5}" for i in rng.sample(range(n), queries)]
    rows, by_variant = [], {}
    for number in numbers:
        for name, text in variants(number).items():
            snapshot, snapshot_ms = timed(lambda: repo.snapshot(scope))
            hits, search_ms = timed(lambda: search(scope, SearchRequest(text=text), snapshot, repo))
            if name == "missing_member":  # there is no such drawing: a member of the same family comes first
                correct = bool(hits) and hits[0].revision_id in {ids[f"{number[:-1]}{m}"] for m in range(5)}
            else:
                correct = bool(hits) and hits[0].revision_id == ids[number]
            rows.append({"variant": name, "number": number, "snapshot_ms": snapshot_ms, "search_ms": search_ms,
                         "results": len(hits), "correct": correct})
            by_variant.setdefault(name, []).append(rows[-1])
    free, free_ms = timed(lambda: search(scope, SearchRequest(text=FREE_TEXT), repo.snapshot(scope), repo))
    series, series_ms = timed(lambda: search(scope, SearchRequest(text=f"{numbers[0][:-2]} 시리즈", top_k=10), repo.snapshot(scope), repo))
    rss_queries = rss_mb()
    summary = {name: {"queries": len(v), "top1_correct": sum(r["correct"] for r in v),
                      "search_ms_median": statistics.median(r["search_ms"] for r in v),
                      "search_ms_max": max(r["search_ms"] for r in v),
                      "snapshot_ms_median": statistics.median(r["snapshot_ms"] for r in v)} for name, v in by_variant.items()}
    out = {"drawings": n, "facts": n * 5, "seed_ms": seed_ms,
           "kept_state": {"cold_snapshot_ms": cold_ms, "warm_snapshot_ms_median": statistics.median(warm_ms),
                          "first_query_ms": first_ms, "free_text_ms": free_ms, "series_query_ms": series_ms,
                          "series_results": len(series)},
           "summary": summary, "free_text": {"query": FREE_TEXT, "results": len(free)},
           "peak_rss_mb": {"after_import": rss_seeded, "after_cold_snapshot": rss_cold, "after_queries": rss_queries},
           "rows": rows}
    if n <= reading_up_to:
        # The same queries through the reading path, and whether it ranks them identically.
        reading, same = [], True
        for name, text in [("as_written", variants(numbers[0])["as_written"]), ("free_text", FREE_TEXT)]:
            snap = repo.snapshot(scope)
            kept = search(scope, SearchRequest(text=text), snap, repo, return_pool=True)
            old, old_ms = timed(lambda: search(scope, SearchRequest(text=text), snap, Reading(repo), return_pool=True))
            same = same and [c.model_dump(mode="json") for c in kept] == [c.model_dump(mode="json") for c in old]
            reading.append({"query": name, "search_ms": old_ms})
        out["reading_path"] = {"queries": reading, "ranked_identically": same, "peak_rss_mb_after": rss_mb()}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", type=int, nargs="+", default=[1000, 10000, 100000])
    ap.add_argument("--queries", type=int, default=10, help="sampled drawing numbers per size (five query forms each)")
    ap.add_argument("--reading", type=int, default=0, metavar="N", help="also time the reading path (and compare its ranking) up to N drawings")
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    import os
    repo = Repository(os.environ["FITWITNESS_DATABASE_URL"])
    repo.migrate()
    results = []
    for n in args.sizes:
        r = measure(repo, n, args.queries, args.seed, args.reading)
        print(json.dumps({k: v for k, v in r.items() if k != "rows"}, ensure_ascii=False), flush=True)
        results.append(r)
    record = {"what": __doc__.split("\n\n")[0].strip(), "measured_at": datetime.now(timezone.utc).isoformat(),
              "code_sha": git_sha(dirty_suffix=True),
              "database": "local PostgreSQL 16 (pgserver), same machine", "seed": args.seed, "results": results}
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
