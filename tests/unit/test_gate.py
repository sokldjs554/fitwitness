from fitwitness.evaluation.gate import THRESHOLDS, compare


def _report(**over):
    base = {"rules_graph": {"accuracy": 1.0}, "lexical": {"exact": {"recall_at_5": 1.0}, "id_variant": {"recall_at_5": 1.0},
            "id_typo": {"recall_at_5": 1.0}, "paraphrase": {"recall_at_5": 0.2}}, "geometry": {"same_family_at_3": 0.9},
            "control": {"id_variant_gap": 1.0, "id_typo_gap": 1.0, "claims_wrong_pay_gap": 0.15, "official_reader_gap": 0.4, "trajectory_gap": 0.25, "trajectory_degraded_violations": 10},
            "claims": {"wrong_pay_rate": 0.0, "wrong_deny_rate": 0.0, "decision_accuracy": 0.92, "field_accuracy": 0.97, "auto_rate": 0.88},
            "claims_official": {"wrong_pay_rate": 0.0, "wrong_deny_rate": 0.0, "decision_accuracy": 0.92, "field_accuracy": 0.98},
            "trajectory": {"match_rate": 1.0, "invariant_violations": 0, "graph_wrong_pay": 0}}
    for path, value in over.items():
        cur = base
        parts = path.split(".")
        for part in parts[:-1]:
            cur = cur[part]
        cur[parts[-1]] = value
    return base


def test_gate_passes_healthy_report_and_fails_regressions():
    good = _report()
    assert compare(good, None)["passed"]
    worse = compare(_report(**{"lexical.id_typo.recall_at_5": 0.5}), good)
    assert not worse["passed"] and any(c["metric"] == "lexical.id_typo.recall_at_5" and not c["ok"] for c in worse["checks"])
    dropped = compare(_report(**{"lexical.paraphrase.recall_at_5": 0.05}), good)
    assert not dropped["passed"] and "dropped" in next(c["why"] for c in dropped["checks"] if c["metric"].startswith("lexical.paraphrase"))


def test_gate_requires_the_degraded_control_to_be_worse():
    toothless = compare(_report(**{"control.id_variant_gap": 0.0}), None)
    assert not toothless["passed"]
    assert {"control.id_variant_gap", "control.id_typo_gap"} <= set(THRESHOLDS)


def test_gate_never_tolerates_a_wrong_payout():
    paid_wrongly = compare(_report(**{"claims.wrong_pay_rate": 0.01}), None)
    assert not paid_wrongly["passed"]
    assert next(c for c in paid_wrongly["checks"] if c["metric"] == "claims.wrong_pay_rate")["why"] == "0.01 != 0.0"
    blind = compare(_report(**{"control.claims_wrong_pay_gap": 0.0}), None)
    assert not blind["passed"]


def test_gate_fails_when_a_run_takes_a_different_road():
    changed = compare(_report(**{"trajectory.match_rate": 0.99}), None)
    assert not changed["passed"]
    broken = compare(_report(**{"trajectory.invariant_violations": 1}), None)
    assert not broken["passed"]
    toothless = compare(_report(**{"control.trajectory_degraded_violations": 0}), None)
    assert not toothless["passed"], "a control that finds nothing wrong proves nothing"


def test_the_scan_suite_is_skipped_without_tesseract_but_never_tolerates_a_wrong_payout_or_refusal():
    skipped = compare(_report(), None)  # no claims_scan in the report: Tesseract was not installed
    assert skipped["passed"] and next(c for c in skipped["checks"] if c["metric"] == "claims_scan.wrong_pay_rate")["why"].startswith("skipped")
    scan = {"claims_scan": {"wrong_pay_rate": 0.0, "wrong_deny_rate": 0.0, "field_accuracy": 0.94, "auto_rate": 0.8}}
    healthy = _report()
    healthy.update(scan)
    assert compare(healthy, None)["passed"]
    for key in ("wrong_pay_rate", "wrong_deny_rate"):
        bad = _report()
        bad.update({"claims_scan": {**scan["claims_scan"], key: 0.02}})
        assert not compare(bad, None)["passed"], key
    worse = _report()
    worse.update({"claims_scan": {**scan["claims_scan"], "field_accuracy": 0.85}})
    assert not compare(worse, healthy)["passed"]


def test_the_official_form_suite_fails_on_a_wrong_payout_or_refusal_and_needs_its_control_to_see_a_gap():
    for key in ("wrong_pay_rate", "wrong_deny_rate"):
        assert not compare(_report(**{f"claims_official.{key}": 0.01}), None)["passed"], key
    assert not compare(_report(**{"claims_official.field_accuracy": 0.90}), None)["passed"]
    blind = compare(_report(**{"control.official_reader_gap": 0.0}), None)
    assert not blind["passed"], "a reader that does no better than the text reader on the forms proves nothing"
