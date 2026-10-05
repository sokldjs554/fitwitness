import pytest
from fitwitness.retrieval.geometry import geometry_distance, rank_geometry


A = {
    "bbox_mm": [10.0, 20.0, 30.0],
    "volume_mm3": 4000.0,
    "surface_mm2": 2200.0,
}
B = {
    "bbox_mm": [10.2, 20.1, 29.8],
    "volume_mm3": 4050.0,
    "surface_mm2": 2210.0,
}
FAR = {
    "bbox_mm": [80.0, 90.0, 100.0],
    "volume_mm3": 400000.0,
    "surface_mm2": 60000.0,
}


def test_geometry_distance_is_zero_symmetric_and_axis_permutation_invariant():
    assert geometry_distance(A, A)["distance"] == pytest.approx(0)
    permuted = {**A, "bbox_mm": [30.0, 10.0, 20.0]}
    assert geometry_distance(A, permuted)["distance"] == pytest.approx(0)
    assert geometry_distance(A, B)["distance"] == pytest.approx(
        geometry_distance(B, A)["distance"]
    )


def test_geometry_ranking_prefers_closest_shape():
    rows = rank_geometry(A, {"far": FAR, "near": B})
    assert [row["candidate_id"] for row in rows] == ["near", "far"]


def test_geometry_distance_rejects_nonphysical_features():
    with pytest.raises(ValueError):
        geometry_distance(A, {**B, "volume_mm3": 0})
