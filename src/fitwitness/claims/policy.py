"""Deterministic adjudication: the company's payout criteria as data, applied by code.

The extraction is untrusted input (a model or a parser produced it); nothing in this
module calls a model. Every won paid and every deny or review carries a rule id, so a
decision can be explained line by line and each rule can be tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import re
from fitwitness.claims.surgery_table import table_grade
from fitwitness.claims.models import ClaimDecision, Coverage, Extraction, LineItem, Policy, Product, Reason

PRODUCTS: dict[str, Product] = {
    "HLTH-A": Product(
        product_id="HLTH-A",
        name="종합건강보험 A형",
        auto_approve_limit=5_000_000,
        second_approval_above=10_000_000,
        coverages={
            "hospitalization_daily": Coverage(kind="hospitalization_daily", per_day=30_000, deductible_days=3, max_days=120),
            "surgery": Coverage(
                kind="surgery",
                surgery_table={1: 100_000, 2: 300_000, 3: 500_000, 4: 1_000_000, 5: 2_000_000},
                exclusion_prefixes=["Q", "Z41"],
            ),
            "diagnosis": Coverage(
                kind="diagnosis",
                diagnosis_table={"C": 10_000_000, "D0": 2_000_000, "I21": 5_000_000, "I6": 5_000_000},
                waiting_days=90,
                exclusion_prefixes=["C44"],
            ),
        },
    ),
    "CANCER-B": Product(
        product_id="CANCER-B",
        name="암진단 플러스 B형",
        auto_approve_limit=20_000_000,
        second_approval_above=30_000_000,
        coverages={
            "diagnosis": Coverage(
                kind="diagnosis", diagnosis_table={"C": 30_000_000, "D0": 6_000_000}, waiting_days=90, exclusion_prefixes=["C44"]
            ),
            "hospitalization_daily": Coverage(kind="hospitalization_daily", per_day=50_000, deductible_days=0, max_days=180),
        },
    ),
}
REQUIRED_DOCS: dict[str, set[str]] = {
    "hospitalization_daily": {"admission"},
    "surgery": {"surgery", "diagnosis"},
    "diagnosis": {"diagnosis"},
}
CONFIDENCE_FLOOR = 0.7
KCD_RE = re.compile(r"^[A-Z][0-9]{2}(\.[0-9]{1,2})?$")


def parse_date(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _prefix(code: str | None, prefixes) -> str | None:
    if not code:
        return None
    for p in prefixes:
        if code.upper().startswith(p.upper()):
            return p
    return None


def validate(ex: Extraction, present_docs: set[str]) -> list[str]:
    """Cross-document consistency flags; the rules turn each into a REVIEW reason."""
    flags: list[str] = []
    a, dch, sg, dx = (
        parse_date(ex.admission_date),
        parse_date(ex.discharge_date),
        parse_date(ex.surgery_date),
        parse_date(ex.diagnosis_date),
    )
    for name, raw in (
        ("admission_date", ex.admission_date),
        ("discharge_date", ex.discharge_date),
        ("surgery_date", ex.surgery_date),
        ("diagnosis_date", ex.diagnosis_date),
    ):
        if raw and parse_date(raw) is None:
            flags.append(f"unparseable_{name}")
    if a and dch and dch < a:
        flags.append("discharge_before_admission")
    if a and sg and dch and not (a <= sg <= dch):
        flags.append("surgery_outside_stay")
    if dx and a and (a - dx).days > 60:
        flags.append("admission_far_from_diagnosis")
    if ex.diagnosis_code and not KCD_RE.match(ex.diagnosis_code):
        flags.append("malformed_diagnosis_code")
    if ex.surgery_grade is not None and not (1 <= ex.surgery_grade <= 5):
        flags.append("surgery_grade_out_of_range")
    listed = table_grade(ex.surgery_name)
    if listed is not None and ex.surgery_grade is not None and 1 <= ex.surgery_grade <= 5 and listed != ex.surgery_grade:
        flags.append("surgery_grade_differs_from_table")  # the printed grade and the table disagree: one reading is wrong
    if ex.total_amount is not None and ex.total_amount <= 0:
        flags.append("nonpositive_amount")
    if "admission" in present_docs and (a is None or dch is None):
        flags.append("admission_doc_without_dates")
    if "surgery" in present_docs and sg is None:
        flags.append("surgery_doc_without_date")
    # Evidence grounding: every populated field must point at a place in a document.
    for k in Extraction.FIELDS:
        if getattr(ex, k) not in (None, "") and k not in ex.evidence:
            flags.append("missing_evidence")
            break
    return flags


CONTINUATION_DAYS = 180  # a stay that begins this many days or fewer after a stay for the same disease ended continues it


@dataclass(frozen=True)
class Stay:
    """A hospital stay that was paid, as the ledger remembers it: its dates and the disease (first three characters of
    the KCD code, "-" when the documents gave none)."""

    admission: date
    discharge: date
    disease: str

    @property
    def nights(self) -> int:
        return (self.discharge - self.admission).days


def stay_key(policy_id: str, admission: str, discharge: str, disease: str) -> str:
    return f"{policy_id}|stay|{admission}|{discharge}|{disease}"


def paid_stays(prior_paid_keys, policy_id: str) -> list[Stay]:
    """The stays the ledger holds for this policy, read back from their keys."""
    stays = []
    for key in prior_paid_keys:
        head, _, rest = key.partition("|stay|")
        if head != policy_id or not rest:
            continue
        admission, discharge, disease = (rest.split("|") + ["-", "-", "-"])[:3]
        a, d = parse_date(admission), parse_date(discharge)
        if a and d and d >= a:
            stays.append(Stay(a, d, disease or "-"))
    return sorted(stays, key=lambda s: (s.admission, s.discharge))


def continued_stays(stays: list[Stay], admission: date, disease: str) -> list[Stay]:
    """The earlier paid stays that this one continues, nearest first: the same disease, each beginning no later than
    ``CONTINUATION_DAYS`` after the previous one ended. A stay with no disease on the documents continues nothing."""
    if disease == "-":
        return []
    chain, anchor = [], admission
    for stay in sorted((s for s in stays if s.disease == disease and s.discharge <= admission), key=lambda s: s.discharge, reverse=True):
        gap = (anchor - stay.discharge).days
        if gap < 0:
            continue  # overlaps the stay already linked
        if gap > CONTINUATION_DAYS:
            break
        chain.append(stay)
        anchor = stay.admission
    return chain


def disease_of(code: str | None) -> str:
    return code[:3].upper() if code else "-"


def dedupe_keys(policy: Policy, ex: Extraction, decision: ClaimDecision) -> list[str]:
    """Keys under which this payout is recorded, so the same event cannot be paid twice."""
    keys = []
    for li in decision.line_items:
        if li.coverage == "hospitalization_daily" and ex.admission_date:
            keys.append(f"{policy.policy_id}|hosp|{ex.admission_date}")
            if parse_date(ex.admission_date) and parse_date(ex.discharge_date):
                keys.append(stay_key(policy.policy_id, ex.admission_date, ex.discharge_date, disease_of(ex.diagnosis_code)))
        elif li.coverage == "surgery" and ex.surgery_date:
            keys.append(f"{policy.policy_id}|surg|{ex.surgery_date}")
        elif li.coverage == "diagnosis" and ex.diagnosis_code:
            keys.append(f"{policy.policy_id}|diag|{ex.diagnosis_code[:3]}")
    return keys


def adjust_items(items: list[LineItem], total: int) -> list[LineItem]:
    """The proposed lines at a person's adjusted total, split in proportion to what was proposed.

    Every proposed coverage stays on the payout, so the event key of each one is written to the ledger: replacing
    the lines by a single line of the first coverage left the other coverages' keys unrecorded."""
    proposed = sum(li.amount for li in items)
    if not items or proposed <= 0:
        return [
            LineItem(
                coverage=items[0].coverage if items else "hospitalization_daily",
                rule_id="R-HUMAN-01",
                amount=total,
                basis=f"담당자 조정 지급액 {total:,}원",
            )
        ]
    shares = [total * li.amount // proposed for li in items]
    shares[0] += total - sum(shares)  # the rounding remainder rides on the first line
    return [
        LineItem(
            coverage=li.coverage,
            rule_id="R-HUMAN-01",
            amount=share,
            basis=f"담당자 조정 지급액 {share:,}원 (심사 제안 {li.amount:,}원 중 {li.coverage})",
        )
        for li, share in zip(items, shares)
    ]


def _scanned_denial(reasons: list[Reason], total: int, items: list[LineItem] | None = None) -> ClaimDecision:
    """A refusal that rests on values read from a picture goes to a person. The machine may pay what it
    read with confidence, or hand the claim over; it does not turn a claimant away on a reading that
    measurably goes wrong now and then (a "D" read as a zero made a payable cancer claim "not covered")."""
    reasons = reasons + [
        Reason(
            rule_id="R-SCAN-01",
            code="denial_on_scanned_document",
            severity="review",
            message="스캔 서류에서 읽은 값에 근거한 부지급은 자동으로 확정하지 않고 담당자가 확인합니다.",
        )
    ]
    return ClaimDecision(outcome="REVIEW", line_items=items or [], total_amount=total, reasons=reasons, needs_human=True)


def _doubtful_denial(reasons: list[Reason], total: int, items: list[LineItem]) -> ClaimDecision:
    """A refusal next to a reading the system itself doubts (extraction confidence under the floor) goes to a
    person: it cannot tell which value is the wrong one, and a claimant is not turned away on a file that was
    partly unreadable. Documents that disagree are a different case: the refusal may rest on fields that agree."""
    reasons = reasons + [
        Reason(
            rule_id="R-DOUBT-01",
            code="denial_on_doubtful_reading",
            severity="review",
            message="서류 판독에 의심이 있는 상태의 부지급은 자동으로 확정하지 않고 담당자가 확인합니다.",
        )
    ]
    return ClaimDecision(outcome="REVIEW", line_items=items, total_amount=total, reasons=reasons, needs_human=True)


# A coverage the rules could not judge for want of a document or a value (each of these is raised with the coverage skipped).
UNJUDGED_CODES = {
    "missing_documents",
    "hospital_dates_missing",
    "surgery_grade_missing",
    "unknown_surgery_grade",
    "diagnosis_missing",
    "exclusion_unchecked",
}


def _unfinished_denial(reasons: list[Reason], total: int, items: list[LineItem]) -> ClaimDecision:
    """A refusal on one coverage next to another that could not be judged yet goes to a person. Refusing the whole claim
    would turn away a claimant who owes nothing more than the missing document; a person can refuse the one coverage
    and hold the claim for the rest."""
    reasons = reasons + [
        Reason(
            rule_id="R-UNDEC-01",
            code="denial_with_undecided_coverage",
            severity="review",
            message="다른 담보가 서류·값 부족으로 아직 판단되지 않은 상태의 부지급은 자동으로 확정하지 않고 담당자가 확인합니다.",
        )
    ]
    return ClaimDecision(outcome="REVIEW", line_items=items, total_amount=total, reasons=reasons, needs_human=True)


def adjudicate(
    policy: Policy,
    product: Product,
    requested: list[str],
    ex: Extraction,
    present_docs: set[str],
    prior_paid_keys: set[str],
    flags: list[str] | None = None,
) -> ClaimDecision:
    reasons: list[Reason] = []
    items: list[LineItem] = []
    eff_from, eff_to = parse_date(policy.effective_from), parse_date(policy.effective_to)
    event_date = parse_date(ex.admission_date) or parse_date(ex.surgery_date) or parse_date(ex.diagnosis_date)

    # Contract-level gates: nothing is payable, no line items are computed.
    if policy.status != "active":
        reasons.append(
            Reason(rule_id="R-POL-02", code="policy_not_active", severity="deny", message=f"계약 상태가 {policy.status} 입니다.")
        )
    if event_date and eff_from and eff_to and not (eff_from <= event_date <= eff_to):
        reasons.append(
            Reason(
                rule_id="R-POL-01",
                code="outside_policy_period",
                severity="deny",
                message=f"사고일({event_date})이 보험기간({eff_from}~{eff_to}) 밖입니다.",
            )
        )
    if reasons:
        if ex.scanned and any(r.rule_id != "R-POL-02" for r in reasons):
            return _scanned_denial(reasons, 0)
        if ex.confidence < CONFIDENCE_FLOOR and any(r.rule_id == "R-POL-01" for r in reasons):
            # the date that puts the event outside the period is the value the reader itself doubts
            reasons.append(
                Reason(
                    rule_id="R-CONF-01",
                    code="low_extraction_confidence",
                    severity="review",
                    message=f"추출 신뢰도 {ex.confidence:.2f} < {CONFIDENCE_FLOOR}",
                )
            )
            return _doubtful_denial(reasons, 0, [])
        return ClaimDecision(outcome="DENY", reasons=reasons, total_amount=0)
    if eff_from is None or eff_to is None:
        # A period that is not a date cannot be checked; paying as if it held would be a guess in the claimant's favour.
        reasons.append(
            Reason(
                rule_id="R-POL-03",
                code="policy_period_unreadable",
                severity="review",
                message=f"계약의 보험기간({policy.effective_from!r}~{policy.effective_to!r})을 날짜로 읽을 수 없어 사고일을 확인할 수 없습니다.",
            )
        )
    if event_date is None:
        reasons.append(
            Reason(
                rule_id="R-REQ-02",
                code="no_event_date",
                severity="review",
                message="사고일(입원/수술/진단일)을 서류에서 확인할 수 없습니다.",
            )
        )
    for flag in flags or []:
        reasons.append(Reason(rule_id="R-CONS-01", code=flag, severity="review", message=f"서류 간 정합성 문제: {flag}"))
    if ex.confidence < CONFIDENCE_FLOOR:
        reasons.append(
            Reason(
                rule_id="R-CONF-01",
                code="low_extraction_confidence",
                severity="review",
                message=f"추출 신뢰도 {ex.confidence:.2f} < {CONFIDENCE_FLOOR}",
            )
        )

    for kind in requested:
        cov = product.coverages.get(kind)
        if cov is None:
            reasons.append(
                Reason(
                    rule_id="R-COV-01", code="coverage_not_in_product", severity="deny", message=f"{kind} 담보는 {product.name}에 없습니다."
                )
            )
            continue
        missing = REQUIRED_DOCS[kind] - present_docs
        if missing:
            reasons.append(
                Reason(
                    rule_id="R-REQ-01",
                    code="missing_documents",
                    severity="review",
                    message=f"{kind} 심사에 필요한 서류 누락: {', '.join(sorted(missing))}",
                )
            )
            continue
        if cov.exclusion_prefixes and kind != "diagnosis" and not ex.diagnosis_code:
            # An exclusion acts on a code that was read; with no code it did not run, so the coverage is not judged.
            reasons.append(
                Reason(
                    rule_id="R-EXC-02",
                    code="exclusion_unchecked",
                    severity="review",
                    message=f"진단코드를 읽지 못해 {kind} 면책 여부를 확인할 수 없습니다.",
                )
            )
            continue
        excl = _prefix(ex.diagnosis_code, cov.exclusion_prefixes)
        if excl:
            reasons.append(
                Reason(
                    rule_id="R-EXC-01",
                    code="excluded_diagnosis",
                    severity="deny",
                    message=f"진단코드 {ex.diagnosis_code}({excl}*)는 {kind} 면책 대상입니다.",
                )
            )
            continue
        if kind == "hospitalization_daily":
            a, dch = parse_date(ex.admission_date), parse_date(ex.discharge_date)
            if not a or not dch:
                reasons.append(
                    Reason(
                        rule_id="R-HOSP-02", code="hospital_dates_missing", severity="review", message="입원/퇴원일을 확인할 수 없습니다."
                    )
                )
                continue
            if f"{policy.policy_id}|hosp|{a.isoformat()}" in prior_paid_keys:
                reasons.append(
                    Reason(rule_id="R-DUP-01", code="duplicate_claim", severity="deny", message=f"{a} 입원 건은 이미 지급되었습니다.")
                )
                continue
            stays = paid_stays(prior_paid_keys, policy.policy_id)
            overlapped = [s for s in stays if s.admission < dch and a < s.discharge]
            if overlapped:
                # The same days would be paid twice. Which claim they belong to is a person's call, not a rule's.
                first = overlapped[0]
                reasons.append(
                    Reason(
                        rule_id="R-HOSP-03",
                        code="overlaps_paid_stay",
                        severity="review",
                        message=f"입원 기간({a}~{dch})이 이미 지급된 입원({first.admission}~{first.discharge})과 겹칩니다.",
                    )
                )
                continue
            nights = (dch - a).days
            chain = continued_stays(stays, a, disease_of(ex.diagnosis_code))
            before = sum(s.nights for s in chain)  # nights of the stays this one continues
            limit = cov.max_days or (before + nights)
            payable = max(0, min(before + nights, limit) - cov.deductible_days) - max(0, min(before, limit) - cov.deductible_days)
            amount = payable * (cov.per_day or 0)
            if chain:
                basis = (
                    f"이어진 입원(같은 질병 직전 {len(chain)}회, 누적 {before}일 + 이번 {nights}일): "
                    f"누적 {before + nights}일 - 면책 {cov.deductible_days}일 중 기지급분을 뺀 {payable}일 × {cov.per_day:,}원"
                )
            else:
                basis = f"입원 {nights}일 - 면책 {cov.deductible_days}일 = {payable}일 × {cov.per_day:,}원"
            if amount > 0:
                items.append(LineItem(coverage=kind, rule_id="R-HOSP-01", amount=amount, basis=basis))
            else:
                reasons.append(
                    Reason(
                        rule_id="R-HOSP-01",
                        code="below_deductible" if not chain else "no_days_left",
                        severity="info",
                        message=(
                            f"입원 {nights}일은 면책 {cov.deductible_days}일 이내입니다."
                            if not chain
                            else f"이어진 입원의 누적 일수가 한도({limit}일)에 이미 닿아 더 지급할 일수가 없습니다."
                        ),
                    )
                )
        elif kind == "surgery":
            if ex.surgery_grade is None or not parse_date(ex.surgery_date):
                listed = table_grade(ex.surgery_name)
                hint = (
                    f" 분류표에는 '{ex.surgery_name}'이(가) {listed}종으로 있습니다."
                    if listed is not None and ex.surgery_grade is None
                    else ""
                )
                reasons.append(
                    Reason(
                        rule_id="R-SURG-02",
                        code="surgery_grade_missing",
                        severity="review",
                        message="수술 등급 또는 수술일을 확인할 수 없습니다." + hint,
                    )
                )
                continue
            if f"{policy.policy_id}|surg|{ex.surgery_date}" in prior_paid_keys:
                reasons.append(
                    Reason(
                        rule_id="R-DUP-01",
                        code="duplicate_claim",
                        severity="deny",
                        message=f"{ex.surgery_date} 수술 건은 이미 지급되었습니다.",
                    )
                )
                continue
            table_amount = cov.surgery_table.get(int(ex.surgery_grade))
            if table_amount is None:
                reasons.append(
                    Reason(
                        rule_id="R-SURG-03",
                        code="unknown_surgery_grade",
                        severity="review",
                        message=f"수술 등급 {ex.surgery_grade}은 분류표에 없습니다.",
                    )
                )
                continue
            items.append(
                LineItem(
                    coverage=kind, rule_id="R-SURG-01", amount=table_amount, basis=f"{ex.surgery_grade}종 수술 정액 {table_amount:,}원"
                )
            )
        elif kind == "diagnosis":
            dd = parse_date(ex.diagnosis_date)
            if not ex.diagnosis_code or not dd:
                reasons.append(
                    Reason(
                        rule_id="R-DIAG-02", code="diagnosis_missing", severity="review", message="진단코드/진단일을 확인할 수 없습니다."
                    )
                )
                continue
            if eff_from and cov.waiting_days and dd < eff_from + timedelta(days=cov.waiting_days):
                reasons.append(
                    Reason(
                        rule_id="R-WAIT-01",
                        code="within_waiting_period",
                        severity="deny",
                        message=f"진단일 {dd}은 면책기간({cov.waiting_days}일, ~{eff_from + timedelta(days=cov.waiting_days)}) 내입니다.",
                    )
                )
                continue
            if f"{policy.policy_id}|diag|{ex.diagnosis_code[:3]}" in prior_paid_keys:
                reasons.append(
                    Reason(
                        rule_id="R-DUP-01",
                        code="duplicate_claim",
                        severity="deny",
                        message=f"{ex.diagnosis_code[:3]} 진단비는 이미 지급되었습니다(1회 한).",
                    )
                )
                continue
            prefix = _prefix(ex.diagnosis_code, sorted(cov.diagnosis_table, key=len, reverse=True))
            if prefix is None:
                reasons.append(
                    Reason(
                        rule_id="R-DIAG-01",
                        code="diagnosis_not_covered",
                        severity="deny",
                        message=f"진단코드 {ex.diagnosis_code}는 진단비 지급 대상이 아닙니다.",
                    )
                )
                continue
            amount = cov.diagnosis_table[prefix]
            items.append(
                LineItem(
                    coverage=kind, rule_id="R-DIAG-01", amount=amount, basis=f"진단코드 {ex.diagnosis_code}({prefix}*) 진단비 {amount:,}원"
                )
            )

    total = sum(li.amount for li in items)
    if total > product.auto_approve_limit:
        reasons.append(
            Reason(
                rule_id="R-AMT-01",
                code="above_auto_limit",
                severity="review",
                message=f"지급액 {total:,}원이 자동승인 한도 {product.auto_approve_limit:,}원을 초과합니다.",
            )
        )
    denied = any(r.severity == "deny" for r in reasons)
    if denied and not items:
        outcome = "DENY"
    elif denied and items:
        outcome = "REVIEW"  # a partial deny next to payable items: a person confirms the split
    elif any(r.severity == "review" for r in reasons):
        outcome = "REVIEW"
    elif not items:
        outcome = "REVIEW" if not reasons else "DENY"
    else:
        outcome = "APPROVE"
    if outcome == "DENY" and ex.scanned:
        return _scanned_denial(reasons, total, items)
    if outcome == "DENY" and any(r.rule_id == "R-CONF-01" for r in reasons):
        return _doubtful_denial(reasons, total, items)
    if outcome == "DENY" and any(r.code in UNJUDGED_CODES for r in reasons):
        return _unfinished_denial(reasons, total, items)
    return ClaimDecision(outcome=outcome, line_items=items, total_amount=total, reasons=reasons, needs_human=(outcome == "REVIEW"))


def explain(decision: ClaimDecision) -> str:
    """A deterministic Korean summary for the customer-facing notice."""
    head = {
        "APPROVE": f"보험금 {decision.total_amount:,}원을 지급합니다.",
        "DENY": "보험금을 지급할 수 없습니다.",
        "REVIEW": "서류 보완이 필요하여 심사를 보류합니다."
        if any(r.code == "info_requested" for r in decision.reasons)
        else "담당자 확인이 필요하여 심사 중입니다.",
    }[decision.outcome]
    lines = [head]
    for li in decision.line_items:
        lines.append(f"- {li.basis} [{li.rule_id}]")
    for r in decision.reasons:
        if r.severity != "info":
            lines.append(f"- {r.message} [{r.rule_id}]")
    return "\n".join(lines)
