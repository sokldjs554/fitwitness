"""Shared, strict input and evidence contracts."""

from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def uid() -> str:
    return str(uuid4())


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Interval(Strict):
    low: Decimal
    high: Decimal

    @field_validator("low", "high")
    @classmethod
    def finite(cls, value):
        if not value.is_finite():
            raise ValueError("finite number required")
        return value

    @model_validator(mode="after")
    def ordered(self):
        if self.low > self.high:
            raise ValueError("inverted interval")
        return self


class TenantScope(Strict):
    tenant_id: str
    user_id: str
    role: Literal["viewer", "operator", "admin"] = "operator"


class SourceRef(Strict):
    revision_id: str
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    page: int | None = Field(default=None, ge=1)
    bbox: tuple[float, float, float, float] | None = None
    feature_id: str | None = None

    @model_validator(mode="after")
    def location(self):
        if self.feature_id is None and (self.page is None or self.bbox is None):
            raise ValueError("a page region or CAD feature is required")
        if self.bbox and (
            not all(0 <= x <= 1 for x in self.bbox)
            or self.bbox[0] >= self.bbox[2]
            or self.bbox[1] >= self.bbox[3]
        ):
            raise ValueError("invalid region")
        return self


class Requirement(Strict):
    id: str = Field(default_factory=uid)
    field: str
    operator: Literal["eq", "range"] = "eq"
    value: str | Interval
    unit: str | None = None
    required: bool = True
    source_text: str = ""


class Fact(Strict):
    id: str = Field(default_factory=uid)
    field: str
    value: str | Interval
    unit: str | None = None
    source: SourceRef
    method: str = "vector_pdf"
    certainty: Literal["verified", "uncertain"] = "verified"


class DrawingRevision(Strict):
    id: str = Field(default_factory=uid)
    tenant_id: str
    document_id: str
    drawing_number: str
    family_id: str
    revision_label: str
    supersedes: str | None = None
    approval: Literal["approved", "draft", "withdrawn"] = "approved"
    effective_from: str = Field(default_factory=now)
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str = ""
    kind: str = "bracket"

    @field_validator("effective_from")
    @classmethod
    def valid_effective_date(cls, value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("timezone-aware effective date required")
        return parsed.astimezone(timezone.utc).isoformat()


class Candidate(Strict):
    revision_id: str
    scores: dict[str, float] = Field(default_factory=dict)
    facts: list[Fact] = Field(default_factory=list)


class Verdict(StrEnum):
    match = "match"
    mismatch = "mismatch"
    unknown = "unknown"


class Evidence(Strict):
    requirement_id: str
    candidate_revision_id: str
    field: str
    fact_ids: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    verdict: Verdict
    summary: str
    verifier_version: str = "interval-v1"


class Decision(Strict):
    revision_id: str
    verdict: Verdict
    evidence: list[Evidence]
    snapshot_id: str
    stale: bool = False
    reviewed_by: str | None = None
    review_note: str | None = None


class ReviewInput(Strict):
    """A reviewer's answer to a drawing run that stopped on unknown verdicts (claims use claims.models.ClaimReview)."""

    decisions: dict[str, Verdict] = Field(default_factory=dict, max_length=50)
    reviewer: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=500)


class SearchRequest(Strict):
    text: str = Field(default="", max_length=4000)
    image_id: str | None = None
    requirements: list[Requirement] = Field(default_factory=list, max_length=20)
    top_k: int = Field(default=6, ge=1, le=50)
    ranking: Literal['rrf', 'cross_encoder', 'constraints'] = 'rrf'


class SearchSnapshot(Strict):
    id: str
    revision_ids: list[str]
    index_hash: str
    created_at: str = Field(default_factory=now)


class Budget(Strict):
    max_model_calls: int = Field(default=8, ge=1, le=20)
    max_tool_calls: int = Field(default=16, ge=1, le=40)
    max_tokens: int = Field(default=16000, ge=1, le=64000)
    max_cost_usd: Decimal = Field(default=Decimal("0.25"), gt=0, le=5)
    deadline_seconds: int = Field(default=120, ge=1, le=600)


class Usage(Strict):
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    cost_usd: Decimal | None = None
    reserved_cost_usd: Decimal = Decimal(0)
    reserved_tokens: int = 0


class RunRequest(Strict):
    demo_fault: bool = False
    # "drawing": search + verify drawings (default). "claim": adjudicate an insurance claim.
    kind: Literal["drawing", "claim"] = "drawing"
    search: SearchRequest = Field(default_factory=SearchRequest)
    claim: dict | None = None  # a claims.models.ClaimRequest, validated by the claim runner
    mode: Literal["fixed", "react", "fitwitness"] = "fitwitness"
    provider: Literal["openai", "anthropic", "rules"] = "rules"
    model_id: str = ""
    budget: Budget = Field(default_factory=Budget)
    # "on_unknown": stop and wait for a reviewer when a required verdict stays unknown.
    review: Literal["none", "on_unknown"] = "none"


class RunView(Strict):
    id: str
    state: Literal[
        "queued",
        "running",
        "waiting_input",
        "retry_wait",
        "completed",
        "failed",
        "cancelled",
        "stale",
    ]
    kind: Literal["drawing", "claim"] = "drawing"
    decisions: list[Decision] = Field(default_factory=list)
    claim: dict | None = None  # a claims.models.ClaimOutcome for kind == "claim"
    question: str | None = None
    usage: Usage = Field(default_factory=Usage)
    error: str | None = None
    input_hash: str
    snapshot_id: str
    provider: str
    model_id: str
    attempts: int = 0
    next_attempt_at: str | None = None
    review_due_at: str | None = None  # a run waiting for a reviewer must be answered by then
    escalated: bool = False  # it was not: it moved to senior handling


class RunEvent(Strict):
    run_id: str
    seq: int
    kind: str
    timestamp: str
    payload: dict
    trace_id: str
