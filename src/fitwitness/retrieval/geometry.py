"""Deterministic CAD feature distance used as a separate retrieval signal."""

from __future__ import annotations
import math

GEOMETRY_WEIGHTS = {"bbox": 0.30, "volume": 0.35, "surface": 0.35}


def _positive(value) -> float:
    number = abs(float(value))
    if not math.isfinite(number) or number <= 0:
        raise ValueError("geometry features must be finite and positive")
    return number


def _log_ratio(left, right) -> float:
    return abs(math.log(_positive(left) / _positive(right)))


def geometry_distance(query: dict, candidate: dict) -> dict[str, float]:
    """Return a symmetric normalized distance and auditable components."""
    q_box = sorted(_positive(value) for value in query["bbox_mm"])
    c_box = sorted(_positive(value) for value in candidate["bbox_mm"])
    if len(q_box) != 3 or len(c_box) != 3:
        raise ValueError("bbox_mm must contain exactly three dimensions")
    bbox = sum(_log_ratio(a, b) for a, b in zip(q_box, c_box)) / 3
    volume = _log_ratio(query["volume_mm3"], candidate["volume_mm3"])
    surface = _log_ratio(query["surface_mm2"], candidate["surface_mm2"])
    distance = (
        GEOMETRY_WEIGHTS["bbox"] * bbox
        + GEOMETRY_WEIGHTS["volume"] * volume
        + GEOMETRY_WEIGHTS["surface"] * surface
    )
    return {
        "distance": distance,
        "bbox": bbox,
        "volume": volume,
        "surface": surface,
    }


def rank_geometry(query: dict, candidates: dict[str, dict]) -> list[dict]:
    rows = []
    for candidate_id, features in candidates.items():
        components = geometry_distance(query, features)
        rows.append({"candidate_id": candidate_id, **components})
    return sorted(rows, key=lambda row: (row["distance"], row["candidate_id"]))
