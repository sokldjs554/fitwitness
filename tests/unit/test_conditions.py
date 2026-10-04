from decimal import Decimal
from uuid import uuid4
import pytest
from pydantic import ValidationError


def modules():
    import importlib.util

    assert importlib.util.find_spec("fitwitness.contracts"), (
        "conditions contract not implemented"
    )
    from fitwitness.contracts import Requirement, Fact, Candidate, SourceRef, Interval
    from fitwitness.verification.conditions import verify

    return Requirement, Fact, Candidate, SourceRef, Interval, verify


def decision(
    value="40",
    unit="mm",
    *,
    need="40",
    need_unit="mm",
    certainty="verified",
    material=None,
):
    R, F, C, S, I, verify = modules()
    revision = str(uuid4())
    ref = S(
        revision_id=revision, source_hash="a" * 64, page=1, bbox=(0.1, 0.1, 0.3, 0.2)
    )
    reqs = [R(field="hole_spacing", value=I(low=need, high=need), unit=need_unit)]
    facts = [
        F(
            field="hole_spacing",
            value=I(low=value, high=value),
            unit=unit,
            source=ref,
            certainty=certainty,
        )
    ]
    if material:
        reqs.append(R(field="material", value=material))
    return verify(reqs, C(revision_id=revision, facts=facts), str(uuid4()))


def test_40mm_rejects_42mm():
    d = decision("42")
    assert d.verdict == "mismatch"
    assert d.evidence[0].source_refs[0].source_hash == "a" * 64


def test_4cm_matches_40mm():
    assert decision("40", need="4", need_unit="cm").verdict == "match"


def test_missing_material_is_unknown():
    assert decision(material="SUS304").verdict == "unknown"


def test_uncertain_ocr_is_unknown():
    assert decision(certainty="uncertain").verdict == "unknown"


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "40,0"])
def test_invalid_number_rejected(bad):
    *_, I, _ = modules()
    with pytest.raises(ValidationError):
        I(low=bad, high=bad)


def test_tolerance_overlap_does_not_prove_containment():
    R, F, C, S, I, verify = modules()
    rev = str(uuid4())
    ref = S(revision_id=rev, source_hash="a" * 64, page=1, bbox=(0, 0, 1, 1))
    d = verify(
        [R(field="length", operator="range", value=I(low="39", high="41"), unit="mm")],
        C(
            revision_id=rev,
            facts=[
                F(field="length", value=I(low="40", high="42"), unit="mm", source=ref)
            ],
        ),
        str(uuid4()),
    )
    assert d.verdict == "mismatch"


def test_empty_requirements_do_not_prove_a_match():
    R, F, C, S, I, verify = modules()
    assert verify([], C(revision_id=str(uuid4())), str(uuid4())).verdict == "unknown"


def test_conflicting_facts_are_unknown():
    R, F, C, S, I, verify = modules()
    rev = str(uuid4())
    ref = S(revision_id=rev, source_hash="a" * 64, page=1, bbox=(0, 0, 1, 1))
    facts = [F(field="material", value=v, source=ref) for v in ["SUS304", "AL6061"]]
    assert (
        verify(
            [R(field="material", value="SUS304")],
            C(revision_id=rev, facts=facts),
            str(uuid4()),
        ).verdict
        == "unknown"
    )


def test_foreign_evidence_never_proves_match():
    R, F, C, S, I, verify = modules()
    ref = S(revision_id=str(uuid4()), source_hash="a" * 64, page=1, bbox=(0, 0, 1, 1))
    assert (
        verify(
            [R(field="material", value="SUS304")],
            C(
                revision_id=str(uuid4()),
                facts=[F(field="material", value="SUS304", source=ref)],
            ),
            str(uuid4()),
        ).verdict
        == "unknown"
    )


def test_unknown_units_hold():
    assert decision(unit="pixels").verdict == "unknown"
