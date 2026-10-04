"""Deterministic conservative verification; no model vote can override facts."""
from decimal import Decimal
from fitwitness.contracts import Candidate, Decision, Evidence, Fact, Interval, Requirement

UNITS = {'mm':Decimal(1), 'cm':Decimal(10), 'm':Decimal(1000), 'in':Decimal('25.4')}


def _compare(req: Requirement, fact: Fact) -> str:
    if fact.certainty != 'verified':
        return 'unknown'
    if isinstance(req.value, Interval) and isinstance(fact.value, Interval):
        if req.unit not in UNITS or fact.unit not in UNITS:
            return 'unknown'
        a,b=UNITS[req.unit],UNITS[fact.unit]
        return 'match' if req.value.low*a<=fact.value.low*b and fact.value.high*b<=req.value.high*a else 'mismatch'
    if isinstance(req.value,str) and isinstance(fact.value,str):
        return 'match' if req.value.strip().casefold()==fact.value.strip().casefold() else 'mismatch'
    return 'unknown'


def verify(requirements: list[Requirement], candidate: Candidate, snapshot_id: str) -> Decision:
    evidence=[]
    for req in requirements:
        facts=[f for f in candidate.facts if f.field==req.field and f.source.revision_id==candidate.revision_id]
        values={(str(f.value),f.unit) for f in facts if f.certainty=='verified'}
        verdict='unknown' if not facts or len(values)!=1 else _compare(req,facts[0])
        label={'match':'확인된 조건 일치','mismatch':'요청 조건과 다름','unknown':'근거 부족 또는 충돌'}[verdict]
        evidence.append(Evidence(requirement_id=req.id,candidate_revision_id=candidate.revision_id,field=req.field,
                                 fact_ids=[f.id for f in facts],source_refs=[f.source for f in facts],verdict=verdict,summary=label))
    required=[e.verdict for e,r in zip(evidence,requirements) if r.required]
    verdict='mismatch' if 'mismatch' in required else 'unknown' if not required or 'unknown' in required else 'match'
    return Decision(revision_id=candidate.revision_id,verdict=verdict,evidence=evidence,snapshot_id=snapshot_id)
