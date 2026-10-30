"""Expected decisions written by hand from the policy table, not produced by ``adjudicate``.

The synthetic claims' gold answers come from the same rule engine the evaluation then scores, so a defect in a rule
cannot show up as a wrong answer there: "0 wrong payments in 120" measures the engine against itself. These cases are
different in one respect only: the outcome and the amount were worked out on paper from the table below (and from
nothing else), before they were put next to the engine. They do not make the engine independent of its author; they
make a rule that disagrees with the written table fail.

    HLTH-A    hospitalization 30,000 a day, first 3 days unpaid, at most 120 days
              surgery by grade 1..5 = 100,000 / 300,000 / 500,000 / 1,000,000 / 2,000,000; no payout for Q* and Z41*
              diagnosis C* 10,000,000, D0* 2,000,000, I21* and I6* 5,000,000; 90 waiting days; no payout for C44*
              a payout above 5,000,000 goes to a person
    CANCER-B  diagnosis C* 30,000,000, D0* 6,000,000, 90 waiting days, no C44*; hospitalization 50,000 a day, no unpaid
              days, at most 180 days; a payout above 20,000,000 goes to a person
    Policy POL-1 runs 2025-01-01 to 2030-01-01.
"""

from fitwitness.claims.models import Extraction, Policy
from fitwitness.claims.policy import PRODUCTS, adjudicate

POL = Policy(policy_id="POL-1", insured_id="I-1", product_id="HLTH-A", effective_from="2025-01-01", effective_to="2030-01-01")
CANCER = POL.model_copy(update={"product_id": "CANCER-B"})
ALL = {"diagnosis", "admission", "surgery", "receipt"}
HOSP, SURG, DIAG = "hospitalization_daily", "surgery", "diagnosis"


# (name, product policy, requested, extraction fields, documents, prior keys, expected outcome, expected total)
CASES = [
    (
        "5 nights, 3 unpaid: 2 x 30,000",
        POL,
        [HOSP],
        dict(admission_date="2025-06-01", discharge_date="2025-06-06"),
        ALL,
        (),
        "APPROVE",
        60_000,
    ),
    ("3 nights are all unpaid", POL, [HOSP], dict(admission_date="2025-06-01", discharge_date="2025-06-04"), ALL, (), "DENY", 0),
    (
        "130 nights stop at 120 days: 117 x 30,000",
        POL,
        [HOSP],
        dict(admission_date="2025-02-01", discharge_date="2025-06-11"),
        ALL,
        (),
        "APPROVE",
        3_510_000,
    ),
    (
        "10 nights on the cancer product: 10 x 50,000",
        CANCER,
        [HOSP],
        dict(admission_date="2025-06-01", discharge_date="2025-06-11"),
        ALL,
        (),
        "APPROVE",
        500_000,
    ),
    ("grade 3 surgery", POL, [SURG], dict(surgery_date="2025-06-02", surgery_grade=3, diagnosis_code="K35.8"), ALL, (), "APPROVE", 500_000),
    (
        "grade 5 surgery",
        POL,
        [SURG],
        dict(surgery_date="2025-06-02", surgery_grade=5, diagnosis_code="K35.8"),
        ALL,
        (),
        "APPROVE",
        2_000_000,
    ),
    (
        "10 nights + grade 2 surgery: 7 x 30,000 + 300,000",
        POL,
        [HOSP, SURG],
        dict(admission_date="2025-06-01", discharge_date="2025-06-11", surgery_date="2025-06-02", surgery_grade=2, diagnosis_code="K35.8"),
        ALL,
        (),
        "APPROVE",
        510_000,
    ),
    (
        "surgery for a congenital condition (Q*) is excluded",
        POL,
        [SURG],
        dict(surgery_date="2025-06-02", surgery_grade=3, diagnosis_code="Q20.1"),
        ALL,
        (),
        "DENY",
        0,
    ),
    (
        "surgery for Z41* is excluded",
        POL,
        [SURG],
        dict(surgery_date="2025-06-02", surgery_grade=3, diagnosis_code="Z41.1"),
        ALL,
        (),
        "DENY",
        0,
    ),
    (
        "surgery with no code read: the exclusions did not run",
        POL,
        [SURG],
        dict(surgery_date="2025-06-02", surgery_grade=3),
        ALL,
        (),
        "REVIEW",
        0,
    ),
    (
        "cancer diagnosis 10,000,000 is above the 5,000,000 limit",
        POL,
        [DIAG],
        dict(diagnosis_code="C16.9", diagnosis_date="2025-06-01"),
        ALL,
        (),
        "REVIEW",
        10_000_000,
    ),
    (
        "I21* diagnosis 5,000,000 is at the limit, not above it",
        POL,
        [DIAG],
        dict(diagnosis_code="I21.9", diagnosis_date="2025-06-01"),
        ALL,
        (),
        "APPROVE",
        5_000_000,
    ),
    ("D0* diagnosis 2,000,000", POL, [DIAG], dict(diagnosis_code="D05.1", diagnosis_date="2025-06-01"), ALL, (), "APPROVE", 2_000_000),
    (
        "C44* is excluded from the diagnosis payout",
        POL,
        [DIAG],
        dict(diagnosis_code="C44.0", diagnosis_date="2025-06-01"),
        ALL,
        (),
        "DENY",
        0,
    ),
    (
        "a code the table does not list is not paid",
        POL,
        [DIAG],
        dict(diagnosis_code="K35.8", diagnosis_date="2025-06-01"),
        ALL,
        (),
        "DENY",
        0,
    ),
    (
        "diagnosed 45 days after the start: inside the 90 waiting days",
        POL,
        [DIAG],
        dict(diagnosis_code="C16.9", diagnosis_date="2025-02-15"),
        ALL,
        (),
        "DENY",
        0,
    ),
    (
        "diagnosed on day 90 (2025-04-01) is no longer inside the waiting days",
        POL,
        [DIAG],
        dict(diagnosis_code="D05.1", diagnosis_date="2025-04-01"),
        ALL,
        (),
        "APPROVE",
        2_000_000,
    ),
    (
        "cancer product: 30,000,000 is above its 20,000,000 limit",
        CANCER,
        [DIAG],
        dict(diagnosis_code="C16.9", diagnosis_date="2025-06-01"),
        ALL,
        (),
        "REVIEW",
        30_000_000,
    ),
    (
        "cancer product: D0* 6,000,000 is under the limit",
        CANCER,
        [DIAG],
        dict(diagnosis_code="D05.1", diagnosis_date="2025-06-01"),
        ALL,
        (),
        "APPROVE",
        6_000_000,
    ),
    (
        "a lapsed policy pays nothing",
        POL.model_copy(update={"status": "lapsed"}),
        [HOSP],
        dict(admission_date="2025-06-01", discharge_date="2025-06-11"),
        ALL,
        (),
        "DENY",
        0,
    ),
    (
        "a stay in 2031 is after the policy ended",
        POL,
        [HOSP],
        dict(admission_date="2031-06-01", discharge_date="2031-06-11"),
        ALL,
        (),
        "DENY",
        0,
    ),
    (
        "no admission document: ask, do not guess",
        POL,
        [HOSP],
        dict(admission_date="2025-06-01", discharge_date="2025-06-11"),
        {"diagnosis", "receipt"},
        (),
        "REVIEW",
        0,
    ),
    (
        "the same admission already paid",
        POL,
        [HOSP],
        dict(admission_date="2025-06-01", discharge_date="2025-06-11"),
        ALL,
        ("POL-1|hosp|2025-06-01",),
        "DENY",
        0,
    ),
    (
        "a reading under the confidence floor never pays by itself",
        POL,
        [HOSP],
        dict(admission_date="2025-06-01", discharge_date="2025-06-11", confidence=0.5),
        ALL,
        (),
        "REVIEW",
        210_000,
    ),
    (
        "a doubtful date outside the period is a person's call",
        POL,
        [HOSP],
        dict(admission_date="2031-06-01", discharge_date="2031-06-11", confidence=0.5),
        ALL,
        (),
        "REVIEW",
        0,
    ),
    (
        "a policy end that is not a date is a person's call",
        POL.model_copy(update={"effective_to": "forever"}),
        [HOSP],
        dict(admission_date="2025-06-01", discharge_date="2025-06-11"),
        ALL,
        (),
        "REVIEW",
        210_000,
    ),
]


def test_the_engine_agrees_with_decisions_worked_out_by_hand_from_the_policy_table():
    wrong = []
    for name, policy, requested, fields, docs, prior, outcome, total in CASES:
        d = adjudicate(policy, PRODUCTS[policy.product_id], requested, Extraction(**fields), set(docs), set(prior), [])
        if (d.outcome, d.total_amount) != (outcome, total):
            wrong.append(f"{name}: wrote {outcome} {total:,}, the engine gave {d.outcome} {d.total_amount:,}")
    assert len(CASES) >= 20
    assert not wrong, "\n".join(wrong)
