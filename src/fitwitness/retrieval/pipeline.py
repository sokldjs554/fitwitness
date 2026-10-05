"""Hybrid retrieval with transparent channel scores and snapshot-scoped candidates."""

from collections import defaultdict
from decimal import Decimal
import re
from rank_bm25 import BM25Okapi
from fitwitness.contracts import Candidate, Interval, Requirement, SearchRequest

KINDS = {
    "브래킷": "bracket",
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


def tokenize(text: str):
    tokens = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*|[가-힣]+", text.casefold())
    aliases = []
    for token in tokens:
        for name, kind in KINDS.items():
            if re.fullmatch(r"[가-힣]+", name) and re.fullmatch(
                re.escape(name) + r"(?:을|를|이|가|은|는|에|용)?", token
            ):
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
        r"\d+(?:\.\d+)?\s*(?:mm|cm|in|kg|MPa|N|m)(?![a-z])", text, re.I
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


def reciprocal_rank_fusion(rankings: dict[str, list[str]], k: int = 60):
    scores = defaultdict(float)
    for ranking in rankings.values():
        for rank, key in enumerate(dict.fromkeys(ranking), 1):
            scores[key] += 1 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def search(
    scope, request: SearchRequest, snapshot, repo, encoders=None, channels=None, reranker=None
) -> list[Candidate]:
    channels = {"exact", "bm25"} if channels is None else channels
    if repo.snapshot(scope).id != snapshot.id:
        raise ValueError("stale search snapshot")
    if channels & {"semantic", "image"}:
        if encoders is None:
            raise ValueError("dense index encoders are not configured")
        from fitwitness.retrieval.indexing import require_index
        require_index(scope, snapshot, repo, encoders)
    allowed = set(snapshot.revision_ids)
    revisions = [r for r in repo.list_revisions(scope) if r.id in allowed]
    if not revisions:
        return []
    facts = repo.load_facts_many(scope, [r.id for r in revisions])
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
    if encoders and request.image_id and "image" in channels:
        data = repo.asset(scope, request.image_id, "png")
        if not data:
            raise ValueError("query image not found")
        vec = encoders.encode_image([data])[0].tolist()
        rows = repo.vector_search(scope, "image", vec, snapshot.revision_ids, 50)
        rankings["image"] = [r["revision_id"] for r in rows]
        for r in rows:
            scores[r["revision_id"]]["image"] = 1 - float(r["distance"])
    ranked = reciprocal_rank_fusion(rankings)
    # Explicit IDs remain the first results, not arbitrary prefix matches.
    exact = set(rankings.get("exact", []))
    ranked.sort(key=lambda kv: (kv[0] not in exact, -kv[1], kv[0]))
    candidates = [
        Candidate(
            revision_id=rid, scores={**scores[rid], "rrf": score}, facts=facts[rid]
        )
        for rid, score in ranked[: max(20, request.top_k) if request.ranking != 'rrf' else request.top_k]
    ]
    if request.ranking == 'constraints':
        from fitwitness.retrieval.reranking import order_constraints
        candidates=order_constraints(candidates,request.requirements + extract_requirements(request.text),snapshot.id)
    elif request.ranking == 'cross_encoder' and request.text.strip() and candidates:
        from fitwitness.retrieval.reranking import order_cross_encoder, configured_reranker
        from fitwitness.retrieval.indexing import document_text
        passages={r.id:document_text(r,facts[r.id]) for r in revisions}
        candidates=order_cross_encoder(candidates,request.text,passages,reranker or configured_reranker())
    if repo.snapshot(scope).id != snapshot.id:
        raise ValueError("search snapshot changed during retrieval")
    return candidates[:request.top_k]
