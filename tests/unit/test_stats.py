"""Intervals for the committed numbers (``evaluation/stats.py``)."""
import pytest
from fitwitness.evaluation import stats as S


def test_wilson_is_wide_at_zero_errors_and_bounded_by_the_unit_interval():
    assert S.wilson(0, 120) == [0.0, 0.031]       # no wrong payment in 120 claims: up to 3.1% is still consistent
    assert S.wilson(72, 72) == [0.9493, 1.0]      # 72/72 verdicts: the lower bound, not 100%, is the claim
    assert S.wilson(18, 18)[0] == pytest.approx(0.8241, abs=1e-4)
    assert S.wilson(11, 11)[0] == pytest.approx(0.7412, abs=1e-4)
    assert S.wilson(1, 0) is None
    low, high = S.wilson(4, 8)
    assert 0.2 < low < 0.5 < high < 0.8


def test_pass_power_k_counts_cases_correct_on_every_repeat_not_verdicts():
    outcomes = {"a": [True, True, True], "b": [True, False, True], "c": [True, True, True], "d": [False, False, False]}
    got = S.pass_power_k(outcomes)
    assert got["cases"] == 4 and got["k"] == 3 and got["pass_power_k"] == 0.5 and got["cases_all_correct"] == 2
    assert got["pass_at_1"] == pytest.approx((1 + 2 / 3 + 1 + 0) / 4, abs=1e-4)
    assert S.pass_power_k({}) == {"cases": 0}


def test_case_bootstrap_resamples_cases_and_is_deterministic():
    values = {f"c{i}": [1.0, 1.0, 1.0] if i < 6 else [0.0, 0.0, 0.0] for i in range(24)}
    first, second = S.case_bootstrap(values), S.case_bootstrap(values)
    assert first == second and first[0] < 0.25 < first[1]
    assert S.case_bootstrap({"only": [1.0]}) == [1.0, 1.0]
    assert S.case_bootstrap({}) is None


def test_sem_needs_two_values():
    assert S.sem([1.0]) is None and S.sem([1.0, 1.0, 1.0]) == 0.0 and S.sem([0.0, 1.0]) == pytest.approx(0.5, abs=1e-4)
