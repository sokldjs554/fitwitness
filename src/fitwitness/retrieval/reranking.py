"""Post-retrieval ordering, never a replacement for final evidence verification."""

import math
from functools import lru_cache
from pathlib import Path
import json
import os
from fitwitness.verification.conditions import verify
from fitwitness.retrieval.embeddings import verify_models


def order_constraints(candidates, requirements, snapshot_id, kinds=None):
    """Order by the stated conditions. ``kinds`` (revision id -> kind) lets a stated part kind count as a
    condition too: the kind is the drawing's recorded fact, not something read from the PDF, so the verifier
    cannot see it and it is matched here."""
    result = []
    wanted = [r for r in requirements if r.field == "kind" and r.required]
    for candidate in candidates:
        decision = verify(requirements, candidate, snapshot_id)
        required = {r.id for r in requirements if r.required}
        counts = {
            v: sum(e.verdict == v and e.requirement_id in required for e in decision.evidence) for v in ("match", "unknown", "mismatch")
        }
        extra = {}
        if wanted and kinds is not None and candidate.revision_id in kinds:
            hit = all(str(r.value) == kinds[candidate.revision_id] for r in wanted)
            counts["match" if hit else "mismatch"] += 1
            counts["unknown"] -= len(wanted)  # the verifier counted them as unknown (no fact to read)
            extra = {"kind_match": 1.0 if hit else 0.0}
        result.append(
            candidate.model_copy(
                update={"scores": {**candidate.scores, **extra, **{"condition_" + k: float(v) for k, v in counts.items()}}}
            )
        )
    # Stable ties retain the original retrieval order. Unknowns are not removed.
    return sorted(result, key=lambda c: (-c.scores.get("exact", 0), c.scores["condition_mismatch"] > 0, -c.scores["condition_match"]))


def widen_pool(ranked, kinds, wanted, size):
    """The fused ranking's first ``size`` entries, then up to ``size`` more of the stated kind from deeper down.
    Nothing leaves the pool; a wrong kind reading can only add candidates that then sort last."""
    pool = list(ranked[:size])
    if wanted:
        pool += [kv for kv in ranked[size:] if kinds.get(kv[0]) in wanted][:size]
    return pool


def order_cross_encoder(candidates, query, passages, model):
    if not query.strip() or not candidates:
        return candidates
    values = model.score(query, [passages[c.revision_id] for c in candidates])
    if len(values) != len(candidates) or not all(math.isfinite(float(x)) for x in values):
        raise ValueError("invalid cross-encoder scores")
    result = [c.model_copy(update={"scores": {**c.scores, "cross_encoder": float(v)}}) for c, v in zip(candidates, values)]
    return sorted(result, key=lambda c: (-c.scores.get("exact", 0), -c.scores["cross_encoder"]))


def reranker_lock():
    return json.loads(Path(__file__).with_name("reranker.json").read_text())


class CrossEncoder:
    def __init__(self):
        import torch
        from transformers import AutoTokenizer, AutoModelForSequenceClassification

        root = Path(os.getenv("FITWITNESS_MODEL_CACHE", "var/models"))
        self.manifest = reranker_lock()
        verify_models(root, self.manifest)
        path = str(root / self.manifest["reranker"]["folder"])
        torch.set_num_threads(int(os.getenv("FITWITNESS_TORCH_THREADS", "2")))
        self.tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
        self.model = AutoModelForSequenceClassification.from_pretrained(path, local_files_only=True).eval().to("cpu")

    def score(self, query, passages):
        import torch

        scores = []
        with torch.inference_mode():
            for start in range(0, len(passages), 4):
                pairs = [[query, p] for p in passages[start : start + 4]]
                inputs = self.tokenizer(pairs, padding=True, truncation=True, max_length=512, return_tensors="pt")
                scores.extend(self.model(**inputs).logits.view(-1).float().tolist())
        return scores


@lru_cache(maxsize=1)
def configured_reranker():
    if os.getenv("FITWITNESS_RERANKER", "off") != "pretrained":
        raise ValueError("cross-encoder is not configured")
    return CrossEncoder()
