"""A model's reading of the query is kept only where it quotes the query, and the harness around it."""

from decimal import Decimal

from fitwitness.contracts import Interval, Requirement, SearchRequest
from fitwitness.evaluation.query_understanding import StubModel, make_queries, oracle, phrasings, relevant, score, summarise
from fitwitness.retrieval.pipeline import extract_requirements, requirements_for
from fitwitness.retrieval.understanding import OPEN_HIGH, Condition, UnderstoodQuery, merge, to_requirements, understand

SOURCE = {"id": "d1", "family_id": "FW-F001", "kind": "bracket"}
TRUTH = {"width": 65, "thickness": 4, "material": "SUS304", "hole_spacing": 40}


def test_a_proposal_is_kept_only_when_its_quote_is_in_the_query():
    query = "브래킷 두께 4mm 이상"
    understood = UnderstoodQuery(
        kind="bracket",
        kind_quote="브래킷",
        conditions=[
            Condition(field="thickness", low="4", high=None, quote="두께 4mm 이상"),
            Condition(field="width", low="65", high="65", quote="폭 65mm"),
        ],
        material="SUS304",
        material_quote="SUS304",
    )
    reqs, dropped = to_requirements(understood, query)
    assert [r.field for r in reqs] == ["kind", "thickness"]
    thickness = reqs[1]
    assert thickness.operator == "range" and thickness.value == Interval(low=Decimal(4), high=OPEN_HIGH) and thickness.unit == "mm"
    assert {d["field"] for d in dropped} == {"width", "material"} and all(d["reason"] == "quote not in query" for d in dropped)


def test_the_longer_grounded_reading_replaces_the_pattern_reading_of_the_same_span():
    query = "브래킷 두께 4mm 이상"
    rules = extract_requirements(query)
    assert any(r.field == "thickness" and r.value == Interval(low=Decimal(4), high=Decimal(4)) for r in rules), (
        "the pattern reads an exact 4mm and flags the rest"
    )
    proposed, _ = to_requirements(UnderstoodQuery(conditions=[Condition(field="thickness", low="4", quote="두께 4mm 이상")]), query)
    merged, changes = merge(rules, proposed)
    thick = [r for r in merged if r.field == "thickness"]
    assert len(thick) == 1 and thick[0].value.high == OPEN_HIGH
    assert changes["replaced"][0]["field"] == "thickness" and changes["replaced"][0]["rules"] == "[4, 4] mm"


def test_an_exclusion_removes_the_material_the_pattern_took_as_wanted():
    query = "브래킷 폭 65mm, AL6061 말고"
    rules = extract_requirements(query)
    assert any(r.field == "material" and r.value == "AL6061" for r in rules), "the pattern reads the excluded material as the wanted one"
    proposed, _ = to_requirements(
        UnderstoodQuery(
            conditions=[Condition(field="width", low="65", high="65", quote="폭 65mm")],
            excluded_materials=["AL6061"],
            excluded_quotes=["AL6061 말고"],
        ),
        query,
    )
    merged, changes = merge(rules, proposed)
    assert not any(r.field == "material" for r in merged)
    excluded = [r for r in merged if r.field == "excluded_material"]
    assert excluded and excluded[0].required is False, "the verifier has no negation, so an exclusion is shown, not enforced"
    assert changes["replaced"][0]["model"] == "excluded AL6061"


def test_a_disagreement_on_a_field_keeps_the_pattern_value_and_is_recorded():
    query = "브래킷 폭 65mm"
    rules = extract_requirements(query)
    proposed, _ = to_requirements(UnderstoodQuery(conditions=[Condition(field="width", low="70", high="70", quote="65mm")]), query)
    merged, changes = merge(rules, proposed)
    widths = [r for r in merged if r.field == "width"]
    assert len(widths) == 1 and widths[0].value.low == Decimal(65)
    assert changes["disagreements"] == [{"field": "width", "rules": ["[65, 65] mm"], "model": "[70, 70] mm", "quote": "65mm"}]


def test_a_search_request_counts_a_condition_once_when_the_intent_already_states_it():
    settled = Requirement(
        field="thickness", operator="range", value=Interval(low=Decimal(4), high=OPEN_HIGH), unit="mm", source_text="두께 4mm 이상"
    )
    reqs = requirements_for(SearchRequest(text="브래킷 두께 4mm 이상", requirements=[settled]))
    assert [r for r in reqs if r.field == "thickness"] == [settled]
    assert any(r.field == "kind" for r in reqs), "what the patterns read elsewhere in the text still comes in"


def test_every_phrasing_is_grounded_and_the_oracle_scores_full_marks():
    queries = phrasings(SOURCE, TRUTH, ["AL6061", "SUS304"])
    assert [q["phrasing"] for q in queries] == [
        "between",
        "cm",
        "at_least",
        "no_unit",
        "english",
        "particle",
        "plain",
        "exclude",
        "unit_word",
        "squeezed",
    ]
    for q in queries:
        reqs, dropped = to_requirements(oracle(q), q["text"])
        assert not dropped, (q["id"], dropped)
        assert score(q["expected"], reqs)["all_correct"], q["id"]


def test_the_patterns_read_the_control_and_miss_the_hard_phrasings():
    queries = {q["phrasing"]: q for q in phrasings(SOURCE, TRUTH, ["AL6061", "SUS304"])}
    assert score(queries["plain"]["expected"], extract_requirements(queries["plain"]["text"]))["all_correct"]
    assert score(queries["cm"]["expected"], extract_requirements(queries["cm"]["text"]))["correct"] == 1, "6.5cm is 65mm to the verifier"
    for pid in ("between", "at_least", "no_unit", "english", "particle", "unit_word", "squeezed"):
        s = score(queries[pid]["expected"], extract_requirements(queries[pid]["text"]))
        assert not s["all_correct"], pid
    assert score(queries["exclude"]["expected"], extract_requirements(queries["exclude"]["text"]))["material_wrong"]


def test_relevance_follows_the_stated_interval_and_the_exclusion():
    active = [{"id": "a", "kind": "bracket"}, {"id": "b", "kind": "bracket"}, {"id": "c", "kind": "flange"}]
    gold = {
        "a": {"width": 65, "thickness": 4, "material": "SUS304"},
        "b": {"width": 65, "thickness": 6, "material": "AL6061"},
        "c": {"width": 65, "thickness": 4, "material": "SUS304"},
    }
    queries = {q["phrasing"]: q for q in phrasings(SOURCE, TRUTH, ["AL6061", "SUS304"])}
    assert set(relevant(queries["at_least"], active, gold)) == {"a", "b"}
    assert set(relevant(queries["exclude"], active, gold)) == {"a"}
    assert set(relevant(queries["no_unit"], active, gold)) == set(), "no gold hole spacing, no relevant drawing"


def test_the_stub_and_the_summary():
    reqs, dropped, meta = understand(StubModel(), "브래킷 폭 65mm")
    assert reqs == [] and dropped == [] and meta["usage"]["input_tokens"] == 2
    row = {
        "phrasing": "plain",
        "rules": score(
            {"kind": "bracket", "conditions": {}, "material": None, "excluded": []}, [Requirement(field="kind", value="bracket")]
        ),
        "model": {
            "dropped": [],
            "replaced": [],
            "disagreements": [],
            **score({"kind": "bracket", "conditions": {}, "material": None, "excluded": []}, []),
        },
        "error": None,
        "latency_ms": 5.0,
        "cost_usd": 0.001,
        "input_tokens": 10,
        "output_tokens": 2,
        "model_errors": [],
        "repairs": 0,
    }
    assert summarise([row], "rules")["all_correct"] == 1 and summarise([row], "model")["all_correct"] == 0
    assert summarise([row], "model")["cost_usd"] == 0.001


def test_make_queries_takes_the_base_drawing_of_each_family_of_the_split():
    manifest = {
        "family_splits": {"dev": ["FW-F001"]},
        "document_entries": [
            {**SOURCE, "is_revision_update": False},
            {"id": "d2", "family_id": "FW-F001", "kind": "bracket", "is_revision_update": True},
        ],
    }
    queries = make_queries(manifest, {"d1": TRUTH, "d2": TRUTH}, "dev")
    assert len(queries) == 9 and all(q["source_id"] == "d1" for q in queries)
    assert "exclude" not in {q["phrasing"] for q in queries}, "one material in the corpus: nothing to exclude"


def test_the_free_form_queries_are_grounded_scored_by_the_oracle_and_split_into_positives_and_negatives():
    from fitwitness.evaluation.query_understanding import freeform_queries

    queries = freeform_queries()
    assert len(queries) == 30 and sum(q["negative"] for q in queries) == 10
    for q in queries:
        reqs, dropped = to_requirements(oracle(q), q["text"])
        assert not dropped, (q["id"], dropped)
        assert score(q["expected"], reqs)["all_correct"], q["id"]
        if q["negative"]:
            assert not q["expected"]["conditions"] and not q["expected"]["material"]
    bound = next(q for q in queries if q["id"] == "FREE-06")
    assert score(bound["expected"], extract_requirements(bound["text"]))["all_correct"] is False, (
        "두께 8 이하 has an open lower end the patterns do not read"
    )
    assert score(bound["expected"], to_requirements(oracle(bound), bound["text"])[0])["correct"] == 1
    count = next(q for q in queries if q["id"] == "FREE-21")
    assert score(count["expected"], extract_requirements(count["text"]))["all_correct"], "the patterns do not turn a count into a condition"


def test_the_practitioner_queries_are_grounded_and_state_no_dimension():
    from fitwitness.evaluation.query_understanding import freeform_queries

    queries = freeform_queries("practitioner")
    assert len(queries) == 10 and all(not q["expected"]["conditions"] for q in queries)
    for q in queries:
        reqs, dropped = to_requirements(oracle(q), q["text"])
        assert not dropped and score(q["expected"], reqs)["all_correct"], q["id"]
    bracket = next(q for q in queries if q["id"] == "PRAC-02")
    assert score(bracket["expected"], extract_requirements(bracket["text"]))["kind_ok"], "브라켓 is read as a bracket now"
    roller = next(q for q in queries if q["id"] == "PRAC-03")
    assert score(roller["expected"], extract_requirements(roller["text"]))["all_correct"], "a part the corpus lacks gets no kind"
