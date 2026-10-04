import importlib.util
from fitwitness.contracts import SearchRequest


def funcs():
    assert importlib.util.find_spec("fitwitness.retrieval.pipeline"), (
        "retrieval pipeline missing"
    )
    from fitwitness.retrieval.pipeline import (
        reciprocal_rank_fusion,
        extract_requirements,
        tokenize,
    )

    return reciprocal_rank_fusion, extract_requirements, tokenize


def test_rrf_rewards_agreement():
    r, _, _ = funcs()
    assert r({"a": ["x", "y"], "b": ["y", "z"]})[0][0] == "y"


def test_drawing_number_is_not_prefix_match():
    _, _, t = funcs()
    assert "bs-120" in t("BS-120 브래킷")
    assert "bs-120" not in t("BS-1200")


def test_korean_condition_parser_converts_cm_without_guessing():
    _, p, _ = funcs()
    rs = p("브래킷 구멍 간격 4cm 재질 SUS304")
    assert {r.field for r in rs} == {"hole_spacing", "material", "kind"}
    r = next(r for r in rs if r.field == "hole_spacing")
    assert r.value.low == 4 and r.unit == "cm"


def test_irrelevant_text_does_not_create_conditions():
    _, p, _ = funcs()
    assert p("설명 없는 부품") == []
