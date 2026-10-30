"""The model auditor's inputs and the measurement around it, without a model."""

from fitwitness.claims.audit import AuditVerdict, Concern, as_reason, payload, terms
from fitwitness.claims.models import ClaimDecision, Extraction, LineItem, Policy, Reason
from fitwitness.claims.policy import PRODUCTS
from fitwitness.evaluation.claims_audit import StubModel, judge, summarise

POLICY = Policy(policy_id="POL-1", insured_id="INS-1", product_id="HLTH-A", effective_from="2025-06-16", effective_to="2030-06-15")
DECISION = ClaimDecision(
    outcome="APPROVE",
    total_amount=210000,
    line_items=[LineItem(coverage="hospitalization_daily", rule_id="R-HOSP-01", amount=210000, basis="7일 × 30,000원")],
)


def test_the_terms_tell_the_auditor_what_the_rules_know():
    text = terms(POLICY, PRODUCTS["HLTH-A"], ["hospitalization_daily", "surgery"], ["POL-1|hosp|2025-12-01"])
    assert "자동 승인 한도: 5,000,000원" in text
    assert "입원일당(hospitalization_daily): 필요 서류 admission; 1일 30,000원, 면책 3일" in text
    assert "수술비(surgery): 필요 서류 diagnosis, surgery" in text
    assert "이미 지급된 항목 키: POL-1|hosp|2025-12-01" in text
    assert "보장 기간 2025-06-16 ~ 2030-06-15" in text


def test_the_payload_carries_terms_rows_values_and_the_decision_in_named_sections():
    ex = Extraction(admission_date="2025-12-07", discharge_date="2025-12-17", confidence=0.9)
    body = payload(
        POLICY,
        PRODUCTS["HLTH-A"],
        ["hospitalization_daily"],
        "[admission] D1\n입원일: 2025-12-07",
        ex,
        DECISION,
        [],
        {"admission", "receipt"},
    )
    for section in ("### 약관", "### 계산된 사실", "### 서류의 줄", "### 읽은 값과 위치", "### 자동 결정"):
        assert section in body
    assert '"admission_date": "2025-12-07"' in body and '"total_amount": 210000' in body and "R-HOSP-01" in body
    assert "입원일수(퇴원일 - 입원일, 박 수): 10일" in body, (
        "the auditor is handed the count the rules use, not left to count inclusive days"
    )
    assert "입원일 2025-12-07: 보장 시작 2025-06-16으로부터 174일 뒤" in body
    assert "입원일당 필요 서류 ['admission']: 모두 있음" in body and "한도 이내" in body and "이미 지급된 것: 없음" in body


def test_the_facts_name_a_missing_document_an_earlier_payment_and_a_total_above_the_limit():
    from fitwitness.claims.audit import facts
    from fitwitness.claims.models import LineItem

    ex = Extraction(admission_date="2025-12-07", discharge_date="2025-12-17", diagnosis_code="C50.9", diagnosis_date="2025-07-01")
    big = ClaimDecision(
        outcome="APPROVE",
        total_amount=31_600_000,
        line_items=[
            LineItem(coverage="diagnosis", rule_id="R-DIAG-01", amount=30_000_000, basis="x"),
            LineItem(coverage="hospitalization_daily", rule_id="R-HOSP-01", amount=1_600_000, basis="y"),
        ],
    )
    text = facts(POLICY, PRODUCTS["CANCER-B"], ["diagnosis", "hospitalization_daily"], {"admission"}, ex, big, ["POL-1|diag|C50"])
    assert "진단비 필요 서류 ['diagnosis']: 없음 ['diagnosis']" in text
    assert "한도 초과" in text and "이미 지급된 것: POL-1|diag|C50" in text
    assert "진단일 2025-07-01: 보장 시작 2025-06-16으로부터 15일 뒤" in text and "면책기간 안" in text


def test_a_non_pay_verdict_becomes_one_review_reason_and_pay_becomes_nothing():
    assert as_reason(AuditVerdict(agree=True, recommendation="pay")) is None
    reason = as_reason(
        AuditVerdict(
            agree=False,
            recommendation="review",
            concerns=[Concern(code="missing_document", message="진단서가 없습니다", evidence="필요 서류 diagnosis")],
        )
    )
    assert (
        isinstance(reason, Reason) and reason.rule_id == "R-AUD-01" and reason.severity == "review" and "missing_document" in reason.message
    )
    deny = as_reason(AuditVerdict(agree=False, recommendation="deny"))
    assert deny.severity == "review", "the auditor can park a claim, never deny it"


def test_judging_a_verdict_against_the_gold_and_summing_up():
    gold = {"outcome": "REVIEW", "total_amount": 0}
    flagged = AuditVerdict(agree=False, recommendation="review", concerns=[Concern(code="missing_document", message="m", evidence="e")])
    assert judge(gold, DECISION, flagged) == {"should_flag": True, "flagged": True, "caught": True, "false_flag": False, "missed": False}
    assert judge(gold, DECISION, None)["missed"] is True
    right = {"outcome": "APPROVE", "total_amount": 210000}
    assert (
        judge(right, DECISION, flagged)["false_flag"] is True
        and judge(right, DECISION, AuditVerdict(agree=True, recommendation="pay"))["flagged"] is False
    )
    rows = [
        {
            "scenario": "missing_doc",
            **judge(gold, DECISION, flagged),
            "verdict": flagged.model_dump(),
            "error": None,
            "latency_ms": 1000.0,
            "cost_usd": 0.004,
            "input_tokens": 3000,
            "output_tokens": 100,
            "model_errors": [],
            "repairs": 0,
        },
        {
            "scenario": "clean",
            **judge(right, DECISION, flagged),
            "verdict": flagged.model_dump(),
            "error": None,
            "latency_ms": 900.0,
            "cost_usd": 0.004,
            "input_tokens": 3000,
            "output_tokens": 100,
            "model_errors": [],
            "repairs": 0,
        },
        {
            "scenario": "clean",
            **judge(right, DECISION, None),
            "verdict": None,
            "error": "boom",
            "latency_ms": 10.0,
            "cost_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "model_errors": [],
            "repairs": 0,
        },
    ]
    s = summarise(rows, "degraded")
    assert (s["audited"], s["wrong_payments"], s["caught"], s["catch_rate"]) == (3, 1, 1, 1.0)
    assert (s["correct_payments"], s["false_flags"], s["false_flag_rate"], s["failed_calls"]) == (2, 1, 0.5, 1)
    assert s["concern_codes_on_caught"] == {"missing_document": 1} and s["by_scenario"]["clean"]["false_flag"] == 1


def test_the_stub_agrees_and_reports_usage():
    verdict, meta = StubModel().structured(AuditVerdict, "p", "x" * 400)
    assert verdict.recommendation == "pay" and meta["usage"]["input_tokens"] == 100


def test_the_subtle_control_pays_a_wrong_amount_that_no_computed_fact_names(tmp_path):
    import json
    from fitwitness.claims.evaluate import decide
    from fitwitness.claims.synth import generate_cases
    from fitwitness.claims.audit import document_rows, facts

    generate_cases(tmp_path, n=22, seed=5)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    gold = json.loads((tmp_path / "gold.json").read_text())
    case = next(
        c
        for c in manifest["cases"]
        if c["scenario"] == "clean" and "surgery" in c["requested"] and "hospitalization_daily" in c["requested"]
    )
    docs = [(d["id"], d["kind"], (tmp_path / case["case_id"] / f"{d['id']}.pdf").read_bytes()) for d in case["documents"]]
    sound_ex, sound, _ = decide(case, docs)
    ex, decision, _ = decide(case, docs, degraded="subtle")
    assert sound.outcome == decision.outcome == "APPROVE" and sound.total_amount == gold[case["case_id"]]["gold"]["total_amount"]
    assert decision.total_amount > sound.total_amount, (
        "one grade up, one night more and no deductible: more money than the documents support"
    )
    assert ex.surgery_grade == sound_ex.surgery_grade + 1 and ex.discharge_date > sound_ex.discharge_date
    policy = Policy.model_validate(case["policy"])
    rows = document_rows(docs)
    assert f"{sound_ex.surgery_grade}종" in rows and sound_ex.discharge_date in rows, "the printed rows still say what the documents say"
    stated = facts(policy, PRODUCTS[policy.product_id], list(case["requested"]), {k for _, k, _ in docs}, ex, decision, [])
    assert "모두 있음" in stated and "한도 이내" in stated and "이미 지급된 것: 없음" in stated, (
        "every fact the auditor is handed looks fine"
    )


def test_the_facts_survive_a_diagnosis_date_that_is_not_a_date():
    from fitwitness.claims.audit import facts

    # a malformed date used to raise TypeError on the waiting-period line, which only the model-audit path reaches
    ex = Extraction(admission_date="2025-12-07", discharge_date="2025-12-17", diagnosis_code="C50.9", diagnosis_date="2026-13-45")
    text = facts(POLICY, PRODUCTS["CANCER-B"], ["diagnosis"], {"diagnosis"}, ex, DECISION, [])
    assert "면책기간" not in text and "필요 서류" in text
