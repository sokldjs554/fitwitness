"""The live model path is off unless every piece is set, and its arithmetic is the operator's ceiling."""

from decimal import Decimal

from fitwitness.api import live

FULL = {
    "FITWITNESS_LIVE_DEMO": "on",
    "ANTHROPIC_API_KEY": "k",
    "FITWITNESS_ANTHROPIC_MODEL": "claude-haiku-5-5",
    "FITWITNESS_ANTHROPIC_INPUT_USD_PER_MILLION": "1",
    "FITWITNESS_ANTHROPIC_OUTPUT_USD_PER_MILLION": "5",
    "FITWITNESS_LIVE_DAILY_USD": "1.00",
    "FITWITNESS_LIVE_RUN_USD": "0.05",
}


def test_it_is_on_only_when_everything_is_set():
    cfg = live.load(FULL)
    assert cfg.enabled and cfg.model == "claude-haiku-5-5" and cfg.run_usd == Decimal("0.05") and cfg.daily_usd == Decimal("1.00")
    assert not live.load({}).enabled
    for missing in FULL:
        assert not live.load({k: v for k, v in FULL.items() if k != missing}).enabled, missing


def test_a_switch_that_is_not_exactly_on_does_not_turn_it_on():
    for value in ("1", "true", "ON", "yes", ""):
        assert not live.load({**FULL, "FITWITNESS_LIVE_DEMO": value}).enabled


def test_a_run_may_not_cost_more_than_the_day_and_garbage_numbers_mean_off():
    assert not live.load({**FULL, "FITWITNESS_LIVE_RUN_USD": "2.00"}).enabled
    for bad in ("0", "-1", "abc", "NaN", "Infinity"):
        assert not live.load({**FULL, "FITWITNESS_LIVE_DAILY_USD": bad}).enabled, bad
        assert not live.load({**FULL, "FITWITNESS_LIVE_RUN_USD": bad}).enabled, bad
        assert not live.load({**FULL, "FITWITNESS_ANTHROPIC_INPUT_USD_PER_MILLION": bad}).enabled, bad


def test_the_runs_left_follow_the_reservation():
    cfg = live.load(FULL)
    assert cfg.remaining_runs(Decimal(0)) == 20 and cfg.remaining_runs(Decimal("0.95")) == 1 and cfg.remaining_runs(Decimal("1.00")) == 0
    assert cfg.remaining_runs(Decimal("5")) == 0 and live.load({}).remaining_runs(Decimal(0)) == 0


def test_the_sessions_share_is_bounded():
    assert live.load({**FULL, "FITWITNESS_LIVE_PER_SESSION": "50"}).per_session == 10
    assert live.load({**FULL, "FITWITNESS_LIVE_PER_SESSION": "0"}).per_session == 1
    assert live.load({**FULL, "FITWITNESS_LIVE_PER_SESSION": "x"}).per_session == 2
