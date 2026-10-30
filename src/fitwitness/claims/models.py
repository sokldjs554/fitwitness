"""Domain types for the claim workflow. Every value here is synthetic."""

from __future__ import annotations

from typing import ClassVar, Literal
from pydantic import Field
from fitwitness.contracts import Strict

CoverageKind = Literal["hospitalization_daily", "surgery", "diagnosis"]
DocKind = Literal["diagnosis", "admission", "surgery", "receipt"]
Outcome = Literal["APPROVE", "DENY", "REVIEW"]
COVERAGE_LABELS = {"hospitalization_daily": "입원일당", "surgery": "수술비", "diagnosis": "진단비"}
DOC_LABELS = {"diagnosis": "진단서", "admission": "입퇴원확인서", "surgery": "수술확인서", "receipt": "진료비 영수증"}
OUTCOME_LABELS = {"APPROVE": "지급", "DENY": "부지급", "REVIEW": "심사자 확인"}


class Coverage(Strict):
    kind: CoverageKind
    per_day: int | None = None  # hospitalization_daily
    deductible_days: int = 0  # the first N days are not paid
    max_days: int | None = None
    surgery_table: dict[int, int] = Field(default_factory=dict)  # grade -> amount
    diagnosis_table: dict[str, int] = Field(default_factory=dict)  # KCD prefix -> lump sum
    waiting_days: int = 0  # days after effective_from before the coverage applies
    exclusion_prefixes: list[str] = Field(default_factory=list)


class Product(Strict):
    product_id: str
    name: str
    coverages: dict[str, Coverage]
    auto_approve_limit: int = 5_000_000  # above this a person signs off
    second_approval_above: int | None = None  # above this the sign-off needs two different people (four eyes)


class Policy(Strict):
    policy_id: str
    insured_id: str
    product_id: str
    effective_from: str  # ISO date
    effective_to: str
    status: Literal["active", "lapsed", "cancelled"] = "active"


class EvidenceRef(Strict):
    """Where a field was read: document, page and normalised bbox, plus the literal text."""

    doc_id: str
    page: int
    bbox: tuple[float, float, float, float]
    snippet: str
    nested: bool = False  # the pair sits inside another line's text (a remark), not in a row of its own


class Extraction(Strict):
    insured_name: str | None = None
    hospital: str | None = None
    diagnosis_name: str | None = None
    diagnosis_code: str | None = Field(None, description="KCD code, e.g. K35.8")
    diagnosis_date: str | None = Field(None, description="YYYY-MM-DD")
    admission_date: str | None = None
    discharge_date: str | None = None
    surgery_name: str | None = None
    surgery_date: str | None = None
    surgery_grade: int | None = Field(None, description="1-5 on the surgery classification table")
    total_amount: int | None = Field(None, description="total billed, KRW")
    evidence: dict[str, EvidenceRef] = Field(default_factory=dict)
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    scanned: bool = Field(False, description="read from a picture by OCR rather than from a text layer")

    FIELDS: ClassVar[tuple[str, ...]] = (
        "insured_name",
        "hospital",
        "diagnosis_name",
        "diagnosis_code",
        "diagnosis_date",
        "admission_date",
        "discharge_date",
        "surgery_name",
        "surgery_date",
        "surgery_grade",
        "total_amount",
    )


class Reason(Strict):
    rule_id: str
    code: str
    message: str
    severity: Literal["deny", "review", "info"]


class LineItem(Strict):
    coverage: CoverageKind
    rule_id: str
    amount: int
    basis: str


class ClaimDecision(Strict):
    outcome: Outcome
    line_items: list[LineItem] = Field(default_factory=list)
    total_amount: int = 0
    reasons: list[Reason] = Field(default_factory=list)
    needs_human: bool = False

    def rule_ids(self) -> list[str]:
        return sorted({r.rule_id for r in self.reasons} | {li.rule_id for li in self.line_items})


class ClaimDocument(Strict):
    """A stored document of a claim case (the bytes live in fw_claim_docs)."""

    id: str
    case_id: str
    kind: DocKind
    issued_at: str
    pages: int = 1


class Resubmission(Strict):
    """A claim run that continues a held one: the same claim, with the documents the claimant sent afterwards."""

    of_run: str  # the held run this one continues
    attempt: int = Field(ge=2)
    added: list[str] = Field(min_length=1)  # supplementary documents now attached
    replaced: list[str] = Field(default_factory=list)  # earlier documents of the same kind that they replace
    held_by: str = ""
    hold_note: str = ""  # what the reviewer asked for


class ClaimSupplement(Strict):
    """The claimant's answer to a request for documents: which of the case's waiting documents to attach."""

    document_ids: list[str] = Field(min_length=1, max_length=4)


class ClaimRequest(Strict):
    """What a claim run works on. Documents are looked up by id inside the tenant."""

    case_id: str
    claim_id: str
    policy: Policy
    requested: list[CoverageKind] = Field(min_length=1, max_length=3)
    document_ids: list[str] = Field(min_length=1, max_length=8)
    submitted_at: str
    review: Literal["rules", "always"] = "rules"  # "always": every claim stops for a reviewer
    audit: Literal["none", "model"] = "none"  # "model": a model reads the decision about to pay and can only send it to a person
    resubmission: Resubmission | None = None  # set when this run continues a claim a reviewer held for documents


class ApprovalReceipt(Strict):
    """The server's signature on one reviewer answer: who answered what, on which run and claim, when, and after which
    earlier answer. Only the API process holds the key; a client cannot send one (it is replaced on arrival)."""

    step: int = Field(ge=1, le=2)
    decided_at: str
    prev: str = ""  # the signature of the first answer when this is the second, else empty
    signature: str  # hex HMAC-SHA256 over the canonical message (claims/receipt.py)
    key_id: str  # which key signed: the first 8 hex digits of its SHA-256


class ClaimReview(Strict):
    """A reviewer's answer for a claim parked in waiting_input."""

    outcome: Literal["APPROVE", "DENY", "REQUEST_INFO"]  # REQUEST_INFO: hold the claim and ask for documents; nothing is paid or refused
    reviewer: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=500)
    total_amount: int | None = Field(None, ge=0, le=10**12)  # an adjusted amount when approving: it can lower the proposal, never raise it
    receipt: ApprovalReceipt | None = None  # set by the API when it accepts the answer
    account: str | None = Field(
        None, max_length=300
    )  # the account an ID token proved (issuer#subject); set by the API, never by the client


class Payout(Strict):
    status: Literal["paid", "already_paid", "skipped", "duplicate"]  # duplicate: the same event was paid under another claim number
    claim_id: str
    amount: int = 0
    paid_at: str | None = None
    run_id: str | None = None
    duplicate_of: str | None = None


class Notice(Strict):
    """A message to the claimant that the system composed. Nothing is sent: there is no mail or phone channel behind it."""

    kind: Literal["request_info", "decision"]
    to: str = "청구인"
    subject: str
    body: str
    respond_by: str | None = None  # request_info: the last day the documents can be added to the held claim (ISO date)
    created_at: str


class ClaimOutcome(Strict):
    """What a finished claim run stores as its result."""

    claim_id: str
    document_ids: list[str] = Field(default_factory=list)  # the documents this run read
    resubmission: Resubmission | None = None
    extraction: Extraction
    flags: list[str] = Field(default_factory=list)
    decision: ClaimDecision
    pre_review_decision: ClaimDecision
    human: ClaimReview | None = None
    approvers: list[str] = Field(default_factory=list)  # everyone who signed off, in order
    approvals: list[ClaimReview] = Field(default_factory=list)  # their answers as accepted, each with the API's receipt
    notices: list[Notice] = Field(default_factory=list)  # what the claimant would be told
    payout: Payout
    explanation: str
    audit: list[str] = Field(default_factory=list)
