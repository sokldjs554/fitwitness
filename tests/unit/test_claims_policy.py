"""Each payout rule on its own, against the policy table."""
from fitwitness.claims.models import Extraction, Policy
from fitwitness.claims.policy import PRODUCTS, adjudicate, adjust_items, dedupe_keys, explain, validate

POL = Policy(policy_id="POL-1", insured_id="I-1", product_id="HLTH-A", effective_from="2025-01-01", effective_to="2030-01-01")
ALL_DOCS = {"diagnosis", "admission", "surgery", "receipt"}


def ex(**kw):
    base = dict(diagnosis_code="K35.8", diagnosis_date="2025-06-01", admission_date="2025-06-01", discharge_date="2025-06-06",
                surgery_date="2025-06-02", surgery_grade=2, insured_name="김민준", hospital="한빛의료원")
    base.update(kw)
    return Extraction(**base)


def run(e, requested=("hospitalization_daily", "surgery"), docs=ALL_DOCS, prior=(), flags=(), policy=POL):
    return adjudicate(policy, PRODUCTS[policy.product_id], list(requested), e, set(docs), set(prior), list(flags))


def test_daily_and_surgery_lines_carry_rule_ids_and_amounts():
    d = run(ex())
    assert d.outcome == "APPROVE" and d.total_amount == 2 * 30_000 + 300_000
    assert d.rule_ids() == ["R-HOSP-01", "R-SURG-01"]
    assert "지급합니다" in explain(d) and "[R-SURG-01]" in explain(d)


def test_deductible_days_are_not_paid():
    d = run(ex(discharge_date="2025-06-03"), requested=("hospitalization_daily",))
    assert d.outcome == "DENY" and d.total_amount == 0 and [r.code for r in d.reasons] == ["below_deductible"]


def test_missing_document_asks_a_person_instead_of_guessing():
    d = run(ex(), docs={"diagnosis", "receipt"})
    assert d.outcome == "REVIEW" and d.total_amount == 0 and {r.code for r in d.reasons} == {"missing_documents"}


def test_exclusions_waiting_period_and_lapsed_policy_deny():
    assert run(ex(diagnosis_code="Z41.1"), requested=("surgery",)).outcome == "DENY"
    cancer = POL.model_copy(update={"product_id": "CANCER-B"})
    early = run(ex(diagnosis_code="C16.9", diagnosis_date="2025-02-01"), requested=("diagnosis",), policy=cancer)
    assert early.outcome == "DENY" and early.reasons[0].rule_id == "R-WAIT-01"
    late = run(ex(diagnosis_code="C16.9", diagnosis_date="2025-06-01"), requested=("diagnosis",), policy=cancer)
    assert late.outcome == "REVIEW" and late.total_amount == 30_000_000  # above the auto-approve limit
    lapsed = run(ex(), policy=POL.model_copy(update={"status": "lapsed"}))
    assert lapsed.outcome == "DENY" and lapsed.line_items == []


def test_duplicates_are_denied_by_dedupe_key():
    paid = run(ex())
    keys = dedupe_keys(POL, ex(), paid)
    assert keys == ["POL-1|hosp|2025-06-01", "POL-1|stay|2025-06-01|2025-06-06|K35", "POL-1|surg|2025-06-02"]
    again = run(ex(), prior=keys)
    assert again.outcome == "DENY" and {r.code for r in again.reasons} == {"duplicate_claim"}


def test_contradictions_and_low_confidence_route_to_review():
    flags = validate(ex(discharge_date="2025-05-30"), ALL_DOCS)
    assert "discharge_before_admission" in flags
    assert run(ex(), flags=flags).outcome == "REVIEW"
    assert run(ex(confidence=0.5)).outcome == "REVIEW"
    assert "missing_evidence" in validate(ex(), ALL_DOCS)  # populated fields without a source position


def test_a_refusal_resting_on_a_scan_goes_to_a_person_but_a_lapsed_policy_is_still_a_refusal():
    excluded = run(ex(diagnosis_code="Z41.1", scanned=True), requested=("surgery",))
    assert excluded.outcome == "REVIEW" and excluded.needs_human and "R-SCAN-01" in excluded.rule_ids()
    assert run(ex(diagnosis_code="Z41.1"), requested=("surgery",)).outcome == "DENY"  # the same claim from a text layer
    early = run(ex(diagnosis_code="C16.9", diagnosis_date="2025-02-01", scanned=True), requested=("diagnosis",), policy=POL.model_copy(update={"product_id": "CANCER-B"}))
    assert early.outcome == "REVIEW" and {"R-WAIT-01", "R-SCAN-01"} <= set(early.rule_ids())
    lapsed = run(ex(scanned=True), policy=POL.model_copy(update={"status": "lapsed"}))
    assert lapsed.outcome == "DENY"  # the contract's status is not something the scan said
    assert run(ex(scanned=True)).outcome == "APPROVE"  # paying what was read with confidence is still automatic


def test_a_refusal_next_to_a_reading_the_system_doubts_goes_to_a_person_but_disagreeing_documents_alone_do_not():
    sure = run(ex(diagnosis_code="Z41.1"), requested=("surgery",))
    assert sure.outcome == "DENY"
    doubtful = run(ex(diagnosis_code="Z41.1", confidence=0.6), requested=("surgery",))
    assert doubtful.outcome == "REVIEW" and doubtful.needs_human and {"R-EXC-01", "R-CONF-01", "R-DOUBT-01"} <= set(doubtful.rule_ids())
    disagreeing = run(ex(diagnosis_code="Z41.1"), requested=("surgery",), flags=["discharge_before_admission"])
    assert disagreeing.outcome == "DENY"  # the refusal rests on a field the documents agree on


def test_a_refusal_next_to_a_coverage_that_could_not_be_judged_goes_to_a_person():
    cancer = POL.model_copy(update={"product_id": "CANCER-B"})  # its diagnosis lump sum is for cancers: I21.9 is refused
    docs = {"diagnosis", "receipt"}  # the hospitalization claim lacks its admission certificate
    asked = run(ex(diagnosis_code="I21.9"), requested=("hospitalization_daily", "diagnosis"), docs=docs, policy=cancer)
    assert asked.outcome == "REVIEW" and asked.needs_human and asked.total_amount == 0
    assert {"R-REQ-01", "R-DIAG-01", "R-UNDEC-01"} <= set(asked.rule_ids())
    # with nothing undecided the same refusal stands, and a missing document alone is still a request for it
    assert run(ex(diagnosis_code="I21.9"), requested=("diagnosis",), policy=cancer).outcome == "DENY"
    assert run(ex(), requested=("hospitalization_daily",), docs=docs).outcome == "REVIEW"
    # a value the documents do not state is the same kind of gap
    no_dates = run(ex(diagnosis_code="I21.9", admission_date=None), requested=("hospitalization_daily", "diagnosis"), policy=cancer)
    assert no_dates.outcome == "REVIEW" and "R-UNDEC-01" in no_dates.rule_ids()


def stay(admission, discharge, disease="K35"):
    return f"POL-1|stay|{admission}|{discharge}|{disease}"


def hospital(prior, **kw):
    return run(ex(**kw), requested=("hospitalization_daily",), prior=prior)


def test_a_stay_that_overlaps_a_paid_one_goes_to_a_person_not_to_a_second_payment():
    d = hospital([stay("2025-06-01", "2025-06-10")], admission_date="2025-06-05", discharge_date="2025-06-12")
    assert d.outcome == "REVIEW" and d.total_amount == 0
    assert [r.code for r in d.reasons] == ["overlaps_paid_stay"] and d.reasons[0].rule_id == "R-HOSP-03"
    # a stay that only touches (leaves the day the other begins) shares no night
    assert hospital([stay("2025-06-01", "2025-06-10")], admission_date="2025-06-10", discharge_date="2025-06-15").outcome == "APPROVE"


def test_a_stay_for_the_same_disease_within_180_days_continues_the_earlier_one():
    # 5 nights paid as 5 - 3 = 2 days; the next 4 nights bring the total to 9 - 3 = 6 days, so 4 more are due
    d = hospital([stay("2025-01-01", "2025-01-06")], admission_date="2025-03-01", discharge_date="2025-03-05")
    assert d.outcome == "APPROVE" and d.total_amount == 4 * 30_000 and "이어진 입원" in d.line_items[0].basis
    # the same 4 nights as a new stay would pay one day: the deductible applies again
    apart = hospital([stay("2025-01-01", "2025-01-06", "J18")], admission_date="2025-03-01", discharge_date="2025-03-05")
    assert apart.total_amount == 1 * 30_000 and "이어진" not in apart.line_items[0].basis
    # beyond 180 days after the discharge it is a new stay too
    far = hospital([stay("2025-01-01", "2025-01-06")], admission_date="2025-07-06", discharge_date="2025-07-10")
    assert far.total_amount == 1 * 30_000
    near = hospital([stay("2025-01-01", "2025-01-06")], admission_date="2025-07-05", discharge_date="2025-07-09")  # day 180
    assert near.total_amount == 4 * 30_000


def test_a_chain_of_stays_shares_one_limit_of_days():
    # 114 + 3 nights so far: 114 days paid of the 117 that 120 days less 3 deductible days allow
    chain = [stay("2025-01-01", "2025-04-25"), stay("2025-05-10", "2025-05-13")]
    d = hospital(chain, admission_date="2025-06-01", discharge_date="2025-06-20")
    assert d.outcome == "APPROVE" and d.total_amount == 3 * 30_000
    full = [stay("2025-01-01", "2025-05-31"), stay("2025-06-05", "2025-06-10")]  # 150 nights: already at the limit
    over = hospital(full, admission_date="2025-06-20", discharge_date="2025-06-25")
    assert over.outcome == "DENY" and [r.code for r in over.reasons] == ["no_days_left"]


def test_a_chain_is_followed_back_through_every_link():
    three = [stay("2024-10-01", "2024-10-05"), stay("2025-01-01", "2025-01-06"), stay("2025-04-01", "2025-04-04")]
    # 4 + 5 + 3 = 12 nights before, 12 - 3 = 9 days paid; this stay's 2 nights make 14 - 3 = 11 days, so 2 more are due
    d = hospital(three, admission_date="2025-06-01", discharge_date="2025-06-03")
    assert d.total_amount == 2 * 30_000 and "직전 3회" in d.line_items[0].basis
    broken = [stay("2024-01-01", "2024-01-05"), stay("2025-01-01", "2025-01-06")]  # the first is more than 180 days before the second
    assert hospital(broken, admission_date="2025-03-01", discharge_date="2025-03-05").total_amount == 4 * 30_000


def test_a_stay_with_no_disease_on_the_documents_continues_nothing():
    d = hospital([stay("2025-01-01", "2025-01-06", "-")], admission_date="2025-03-01", discharge_date="2025-03-05", diagnosis_code=None)
    assert d.total_amount == 1 * 30_000


def test_another_policys_stays_do_not_count():
    other = ["POL-2|stay|2025-06-01|2025-06-10|K35"]
    assert hospital(other, admission_date="2025-06-05", discharge_date="2025-06-12").outcome == "APPROVE"


def test_malformed_ledger_keys_are_ignored_not_trusted():
    junk = ["POL-1|stay|", "POL-1|stay|x|y|z", "POL-1|stay|2025-06-10|2025-06-01|K35"]  # the last ends before it begins
    assert hospital(junk, admission_date="2025-06-05", discharge_date="2025-06-12").outcome == "APPROVE"


def test_a_surgery_grade_that_disagrees_with_the_classification_table_goes_to_a_person():
    from fitwitness.claims.surgery_table import SURGERY_GRADES, table_grade

    assert table_grade("충수절제술") == 2 and table_grade("충수 절제술") == 2 and table_grade("맹장수술") is None
    agree = ex(surgery_name="충수절제술", surgery_grade=2)
    assert "surgery_grade_differs_from_table" not in validate(agree, ALL_DOCS)
    wrong = ex(surgery_name="충수절제술", surgery_grade=4)  # a misread "2종" would pay 1,000,000 won instead of 300,000
    flags = validate(wrong, ALL_DOCS)
    assert "surgery_grade_differs_from_table" in flags
    held = run(wrong, requested=("surgery",), flags=flags)
    assert held.outcome == "REVIEW" and held.total_amount == 1_000_000 and any(r.code == "surgery_grade_differs_from_table" for r in held.reasons)
    # a name the table does not list is not a disagreement: the printed grade stands, as it did before the table existed
    assert "surgery_grade_differs_from_table" not in validate(ex(surgery_name="맹장수술", surgery_grade=4), ALL_DOCS)
    assert set(SURGERY_GRADES.values()) <= {1, 2, 3, 4, 5}


def test_a_missing_grade_tells_the_reviewer_what_the_table_says():
    d = run(ex(surgery_name="담낭절제술", surgery_grade=None), requested=("surgery",))
    assert d.outcome == "REVIEW" and "3종" in d.reasons[0].message and "담낭절제술" in d.reasons[0].message


def test_the_document_generator_prints_the_tables_grades():
    from fitwitness.claims import vocab
    from fitwitness.claims.surgery_table import SURGERY_GRADES

    assert {name: grade for name, grade in vocab.SURGERIES.values()} == SURGERY_GRADES


def test_a_policy_period_that_is_not_a_date_goes_to_a_person_instead_of_skipping_the_period_check():
    """effective_to is text in the policy record; "forever" used to switch the period check off and pay."""
    for field, bad in (("effective_to", "forever"), ("effective_from", ""), ("effective_to", "2030-13-45")):
        d = run(ex(), policy=POL.model_copy(update={field: bad}))
        assert d.outcome == "REVIEW" and d.needs_human, (field, bad)
        assert "policy_period_unreadable" in {r.code for r in d.reasons}


def test_a_doubtful_event_date_outside_the_period_is_a_persons_call_not_an_automatic_denial():
    """The contract gate used to return before the confidence floor was looked at: a date the reader itself doubted,
    read outside the period, denied the claim with nobody asked."""
    outside = dict(admission_date="2031-01-01", discharge_date="2031-01-06", surgery_date="2031-01-02", diagnosis_date="2031-01-01")
    doubtful = run(ex(confidence=0.5, **outside))
    assert doubtful.outcome == "REVIEW" and doubtful.needs_human and doubtful.total_amount == 0
    assert {"R-POL-01", "R-CONF-01", "R-DOUBT-01"} <= set(doubtful.rule_ids())
    sure = run(ex(**outside))
    assert sure.outcome == "DENY" and not sure.needs_human  # a confident reading outside the period is still a denial


def test_a_surgery_whose_diagnosis_code_was_not_read_cannot_pass_the_exclusion_check_by_default():
    """Exclusions (Q*, Z41*) only act on a code that was read; a claim with no readable code used to pay 300,000."""
    d = run(ex(diagnosis_code=None), requested=("surgery",))
    assert d.outcome == "REVIEW" and d.needs_human and d.line_items == []
    assert [r.code for r in d.reasons] == ["exclusion_unchecked"]
    assert run(ex(diagnosis_code=None), requested=("hospitalization_daily",)).outcome == "APPROVE"  # no exclusion applies there


def test_a_denial_next_to_a_surgery_with_no_readable_code_is_a_persons_call():
    d = run(ex(diagnosis_code=None, discharge_date="2025-06-03"))
    assert d.outcome == "REVIEW" and d.needs_human


def test_an_adjusted_approval_keeps_every_coverage_so_every_event_key_is_recorded():
    """A person who lowers the amount used to replace the lines by one line of the first coverage; the other
    coverage's event key was then never written, and the same surgery could be claimed again under another number."""
    d = run(ex())
    items = adjust_items(d.line_items, 200_001)
    assert sum(li.amount for li in items) == 200_001
    assert {li.coverage for li in items} == {"hospitalization_daily", "surgery"} and all(li.amount > 0 for li in items)
    adjusted = d.model_copy(update={"line_items": items, "total_amount": 200_001})
    assert dedupe_keys(POL, ex(), adjusted) == dedupe_keys(POL, ex(), d)
    assert adjust_items([], 5_000)[0].amount == 5_000  # nothing proposed: one plain line, as before
