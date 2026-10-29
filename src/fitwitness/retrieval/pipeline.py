"""Hybrid retrieval with transparent channel scores and snapshot-scoped candidates."""

from collections import defaultdict
import heapq
import re
from rank_bm25 import BM25Okapi
from fitwitness.contracts import Candidate, Interval, Requirement, SearchRequest

KINDS = {
    "브래킷": "bracket",
    "브라켓": "bracket",  # the workshop spelling: a production engineer's first search phrase used it (docs/user-test)
    "지지대": "bracket",
    "bracket": "bracket",
    "플랜지": "flange",
    "flange": "flange",
    "샤프트": "shaft",
    "축": "shaft",
    "shaft": "shaft",
    "하우징": "housing",
    "housing": "housing",
}


_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*|[가-힣]+")
_HANGUL = re.compile(r"[가-힣]+")
# Korean kind names with the particles a query may attach, compiled once: tokenize() runs on
# every indexed text for every search, and compiling these per token dominated its cost.
_KOREAN_KINDS = [
    (name, kind, re.compile(re.escape(name) + r"(?:을|를|이|가|은|는|에|용)?"))
    for name, kind in KINDS.items()
    if _HANGUL.fullmatch(name)
]


def tokenize(text: str):
    tokens = _TOKEN.findall(text.casefold())
    aliases = []
    for token in tokens:
        if not _HANGUL.fullmatch(token):
            continue
        for name, kind, pattern in _KOREAN_KINDS:
            if pattern.fullmatch(token):
                aliases.extend([name, kind])
    return tokens + [alias for alias in dict.fromkeys(aliases) if alias not in tokens]


def extract_requirements(text: str) -> list[Requirement]:
    reqs = []
    # A bare number is deliberately not interpreted as a dimension.
    patterns = {
        "hole_spacing": r"구멍\s*간격|간격|hole[ _-]*spacing",
        "thickness": r"두께|thickness",
        "width": r"너비|폭|width",
        "height": r"높이|height",
    }
    covered = []
    for field, pattern in patterns.items():
        for m in re.finditer(
            r"(?:" + pattern + r")\s*(\d+(?:\.\d+)?)\s*(mm|cm|in|m)(?![a-z])",
            text,
            re.I,
        ):
            reqs.append(
                Requirement(
                    field=field,
                    value=Interval(low=m[1], high=m[1]),
                    unit=m[2].lower(),
                    source_text=m[0],
                )
            )
            covered.append(m.span())
    for m in re.finditer(
        r"(?<![\d.])\d{1,12}(?:\.\d{1,12})?\s*(?:mm|cm|in|kg|MPa|N|m)(?![a-z])", text, re.I  # bounded and anchored: a long run of digits was quadratic
    ):
        if not any(a <= m.start() and m.end() <= b for a, b in covered):
            reqs.append(
                Requirement(field="unparsed_constraint", value=m[0], source_text=m[0])
            )
    if re.search(r"이상|이하|초과|미만|제외|아닌|not|except|[<>≤≥±~]", text, re.I):
        reqs.append(
            Requirement(field="unparsed_constraint", value=text, source_text=text)
        )
    for key, kind in KINDS.items():
        if key in tokenize(text):
            reqs.append(Requirement(field="kind", value=kind, source_text=key))
            break
    m = re.search(r"\b(SUS\s*304|AL\s*6061)\b", text, re.I)
    if m:
        reqs.append(
            Requirement(
                field="material",
                value=re.sub(r"\s+", "", m[1]).upper(),
                source_text=m[0],
            )
        )
    return reqs


def requirements_for(request: SearchRequest) -> list[Requirement]:
    """What the request states plus what its text says. A stated condition that quotes a span of the text
    stands in for the pattern reading of that span (the intent node's model reading, which may have read a
    longer span: "두께 4mm 이상" over "두께 4mm"), so a condition is never counted twice."""
    out = list(request.requirements)
    seen = {(r.field, r.operator, str(r.value), r.unit, r.required) for r in out}
    spans = [_squash(r.source_text) for r in out if r.source_text]
    for r in extract_requirements(request.text):
        key = (r.field, r.operator, str(r.value), r.unit, r.required)
        if key in seen or (r.source_text and any(_squash(r.source_text) in span for span in spans)):
            continue
        seen.add(key)
        out.append(r)
    return out


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def reciprocal_rank_fusion(rankings: dict[str, list[str]], k: int = 60):
    scores = defaultdict(float)
    for ranking in rankings.values():
        for rank, key in enumerate(dict.fromkeys(ranking), 1):
            scores[key] += 1 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def _bm25(state):
    """The BM25 index over the state's texts, built on first use and kept with the state."""
    with state.lock:
        if "bm25" not in state.memo:
            index = BM25Okapi([tokenize(t) or ["_"] for t in state.texts])
            state.memo["bm25"] = index
        return state.memo["bm25"]


def _number_index(state):
    from fitwitness.retrieval.drawing_number import NumberIndex

    with state.lock:
        if "numbers" not in state.memo:
            state.memo["numbers"] = NumberIndex(state.numbers)
        return state.memo["numbers"]


def _search_indexed(scope, request, snapshot, repo, state, encoders, channels, reranker, id_matching, return_pool):
    """``search`` over a ``CorpusState`` the repository keeps until the corpus changes. It ranks exactly as the
    reading path below does (the tests compare them); what differs is what it touches: the drawing numbers and the
    BM25 index are built once per corpus change, and only the candidates that leave it have their facts read."""
    if state.snapshot.id != snapshot.id:
        raise ValueError("stale search snapshot")
    if channels & {"semantic", "image", "image_dino"}:
        if encoders is None:
            raise ValueError("dense index encoders are not configured")
        from fitwitness.retrieval.indexing import dense_channels, require_index

        # Whether every active drawing has its vectors, made by these encoders, is checked once per corpus state: a vector
        # write is a write to the corpus (it moves the change counter), so a state that passed stays true until it is replaced.
        key = ("dense_index_ok", encoders.fingerprint, tuple(sorted(dense_channels(encoders))))
        if not state.memo.get(key):
            require_index(scope, snapshot, repo, encoders)
            state.memo[key] = True
    if not state.ids:
        return []
    ids = state.ids
    rankings = {}
    scores = defaultdict(dict)
    tokens = tokenize(request.text)
    if "exact" in channels:
        if id_matching == "normalized":
            from fitwitness.retrieval.drawing_number import match_text

            hits = match_text(request.text, _number_index(state))
            positions = state.positions_by_number()
            matched_ids = sorted(
                ((ids[i], *hits[number]) for number in hits for i in positions.get(number, ())),
                key=lambda x: (-x[1], x[0]),
            )
            rankings["exact"] = [rid for rid, _, _ in matched_ids]
            for rid, score, mode in matched_ids:
                scores[rid]["exact"] = score
                scores[rid]["id_mode"] = {"exact": 1.0, "series": 0.5, "fuzzy": 0.25}[mode]
        else:
            wanted = set(tokens)
            exact = [ids[i] for i, number in enumerate(state.numbers) if number.casefold() in wanted]
            rankings["exact"] = exact
            for rid in exact:
                scores[rid]["exact"] = 1.0
    if "bm25" in channels and tokens:
        index = _bm25(state)
        vals = index.get_scores(tokens)
        wanted = set(tokens)
        matched = [i for i, freqs in enumerate(index.doc_freqs) if any(t in freqs for t in wanted)]
        top = heapq.nsmallest(50, matched, key=lambda i: (-vals[i], ids[i]))
        rankings["bm25"] = [ids[i] for i in top]
        for i in top:
            scores[ids[i]]["bm25"] = float(vals[i])
    if encoders and request.text.strip() and "semantic" in channels:
        vec = encoders.encode_text([request.text], query=True)[0].tolist()
        rows = repo.vector_search(scope, "text", vec, snapshot.revision_ids, 50)
        rankings["semantic"] = [r["revision_id"] for r in rows]
        for r in rows:
            scores[r["revision_id"]]["semantic"] = 1 - float(r["distance"])
    if encoders and request.image_id and channels & {"image", "image_dino"}:
        data = repo.asset(scope, request.image_id, "png")
        if not data:
            raise ValueError("query image not found")
        for channel, encode in (("image", encoders.encode_image), ("image_dino", getattr(encoders, "encode_image_dino", None))):
            if channel not in channels or encode is None:
                continue
            vec = encode([data])[0].tolist()
            rows = repo.vector_search(scope, channel, vec, snapshot.revision_ids, 50)
            rankings[channel] = [r["revision_id"] for r in rows]
            for r in rows:
                scores[r["revision_id"]][channel] = 1 - float(r["distance"])
    ranked = reciprocal_rank_fusion(rankings)
    exact = set(rankings.get("exact", []))
    ranked.sort(key=lambda kv: (kv[0] not in exact, -scores[kv[0]].get("exact", 0.0), -kv[1], kv[0]))
    pool = ranked[: max(20, request.top_k) if request.ranking != 'rrf' else request.top_k]
    if request.ranking == 'constraints':
        from fitwitness.retrieval.reranking import widen_pool
        stated = requirements_for(request)
        kinds = state.kind_by_id()
        pool = widen_pool(ranked, kinds, {str(r.value) for r in stated if r.field == "kind" and r.required}, max(20, request.top_k))
    facts = repo.load_facts_many(scope, [rid for rid, _ in pool])
    candidates = [Candidate(revision_id=rid, scores={**scores[rid], "rrf": score}, facts=facts[rid]) for rid, score in pool]
    if request.ranking == 'constraints':
        from fitwitness.retrieval.reranking import order_constraints
        candidates = order_constraints(candidates, stated, snapshot.id, kinds=kinds)
    elif request.ranking == 'cross_encoder' and request.text.strip() and candidates:
        from fitwitness.retrieval.reranking import order_cross_encoder, configured_reranker
        from fitwitness.retrieval.indexing import document_text
        revisions = {r.id: r for r in repo.revisions_many(scope, [c.revision_id for c in candidates])}
        passages = {c.revision_id: document_text(revisions[c.revision_id], facts[c.revision_id]) for c in candidates}
        candidates = order_cross_encoder(candidates, request.text, passages, reranker or configured_reranker())
    if repo.snapshot(scope).id != snapshot.id:
        raise ValueError("search snapshot changed during retrieval")
    return candidates if return_pool else candidates[:request.top_k]


def search(
    scope, request: SearchRequest, snapshot, repo, encoders=None, channels=None, reranker=None,
    id_matching: str = "normalized", return_pool: bool = False,
) -> list[Candidate]:
    """Hybrid search over the active snapshot.

    ``id_matching`` selects how the exact channel reads drawing numbers: ``"normalized"``
    (default) understands spacing, underscores, confusable characters, series references
    and one-edit typos; ``"token"`` is the historical casefolded-token match, kept for the
    evaluation gate's degraded control. ``return_pool`` returns the whole ordered candidate pool
    instead of the first ``top_k``: the evaluation reads the bound an ordering can reach from it."""
    channels = {"exact", "bm25"} if channels is None else channels
    corpus_state = getattr(repo, "corpus_state", None)
    if corpus_state is not None:
        return _search_indexed(scope, request, snapshot, repo, corpus_state(scope), encoders, channels, reranker,
                               id_matching, return_pool)
    corpus = getattr(repo, "corpus", None)
    if corpus is not None:
        # Staleness check, revisions and facts in one connection.
        current, revisions, facts = corpus(scope)
        if current.id != snapshot.id:
            raise ValueError("stale search snapshot")
    else:
        if repo.snapshot(scope).id != snapshot.id:
            raise ValueError("stale search snapshot")
        revisions = facts = None
    if channels & {"semantic", "image", "image_dino"}:
        if encoders is None:
            raise ValueError("dense index encoders are not configured")
        from fitwitness.retrieval.indexing import require_index
        require_index(scope, snapshot, repo, encoders)
    allowed = set(snapshot.revision_ids)
    if revisions is None:
        revisions = [r for r in repo.list_revisions(scope) if r.id in allowed]
        facts = repo.load_facts_many(scope, [r.id for r in revisions]) if revisions else {}
    else:
        revisions = [r for r in revisions if r.id in allowed]
    if not revisions:
        return []
    texts = [
        r.title
        + " "
        + r.drawing_number
        + " "
        + r.kind
        + " "
        + " ".join(f"{f.field} {f.value}" for f in facts[r.id])
        for r in revisions
    ]
    rankings = {}
    scores = defaultdict(dict)
    tokens = tokenize(request.text)
    if "exact" in channels:
        if id_matching == "normalized":
            from fitwitness.retrieval.drawing_number import match_text

            hits = match_text(request.text, [r.drawing_number for r in revisions])
            matched_ids = sorted(
                ((r.id, *hits[r.drawing_number]) for r in revisions if r.drawing_number in hits),
                key=lambda x: (-x[1], x[0]),
            )
            rankings["exact"] = [rid for rid, _, _ in matched_ids]
            for rid, score, mode in matched_ids:
                scores[rid]["exact"] = score
                scores[rid]["id_mode"] = {"exact": 1.0, "series": 0.5, "fuzzy": 0.25}[mode]
        else:
            exact = [r.id for r in revisions if r.drawing_number.casefold() in tokens]
            rankings["exact"] = exact
            for rid in exact:
                scores[rid]["exact"] = 1.0
    if "bm25" in channels and tokens:
        vals = BM25Okapi([tokenize(t) or ["_"] for t in texts]).get_scores(tokens)
        ordered = sorted(zip(revisions, vals), key=lambda rv: (-rv[1], rv[0].id))
        matched = {
            r.id for r, t in zip(revisions, texts) if set(tokens) & set(tokenize(t))
        }
        rankings["bm25"] = [r.id for r, v in ordered if r.id in matched][:50]
        for r, v in ordered:
            if r.id in matched:
                scores[r.id]["bm25"] = float(v)
    if encoders and request.text.strip() and "semantic" in channels:
        vec = encoders.encode_text([request.text], query=True)[0].tolist()
        rows = repo.vector_search(scope, "text", vec, snapshot.revision_ids, 50)
        rankings["semantic"] = [r["revision_id"] for r in rows]
        for r in rows:
            scores[r["revision_id"]]["semantic"] = 1 - float(r["distance"])
    if encoders and request.image_id and channels & {"image", "image_dino"}:
        data = repo.asset(scope, request.image_id, "png")
        if not data:
            raise ValueError("query image not found")
        # Two image channels over the same picture: OpenCLIP, and DINOv2 when the encoders carry it.
        for channel, encode in (("image", encoders.encode_image), ("image_dino", getattr(encoders, "encode_image_dino", None))):
            if channel not in channels or encode is None:
                continue
            vec = encode([data])[0].tolist()
            rows = repo.vector_search(scope, channel, vec, snapshot.revision_ids, 50)
            rankings[channel] = [r["revision_id"] for r in rows]
            for r in rows:
                scores[r["revision_id"]][channel] = 1 - float(r["distance"])
    ranked = reciprocal_rank_fusion(rankings)
    # Explicit IDs remain the first results, not arbitrary prefix matches.
    exact = set(rankings.get("exact", []))
    ranked.sort(key=lambda kv: (kv[0] not in exact, -scores[kv[0]].get("exact", 0.0), -kv[1], kv[0]))
    pool = ranked[: max(20, request.top_k) if request.ranking != 'rrf' else request.top_k]
    if request.ranking == 'constraints':
        from fitwitness.retrieval.reranking import widen_pool
        stated = requirements_for(request)
        kinds = {r.id: r.kind for r in revisions}
        # A stated part kind brings candidates of that kind from deeper in the fused ranking into the pool.
        pool = widen_pool(ranked, kinds, {str(r.value) for r in stated if r.field == "kind" and r.required}, max(20, request.top_k))
    candidates = [
        Candidate(
            revision_id=rid, scores={**scores[rid], "rrf": score}, facts=facts[rid]
        )
        for rid, score in pool
    ]
    if request.ranking == 'constraints':
        from fitwitness.retrieval.reranking import order_constraints
        candidates=order_constraints(candidates,stated,snapshot.id,kinds=kinds)
    elif request.ranking == 'cross_encoder' and request.text.strip() and candidates:
        from fitwitness.retrieval.reranking import order_cross_encoder, configured_reranker
        from fitwitness.retrieval.indexing import document_text
        passages={r.id:document_text(r,facts[r.id]) for r in revisions}
        candidates=order_cross_encoder(candidates,request.text,passages,reranker or configured_reranker())
    if repo.snapshot(scope).id != snapshot.id:
        raise ValueError("search snapshot changed during retrieval")
    return candidates if return_pool else candidates[:request.top_k]
