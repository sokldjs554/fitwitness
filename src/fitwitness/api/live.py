"""Live model reading in the public demo: off unless the operator turns it on, and bounded by money.

The public demo has never been allowed to spend: a visitor starts a claim and it is read by rules, and the model runs
the demo shows are recordings of runs made from the command line. This module is how an operator lets visitors run the
model-reading path of a claim (the extractor, with the same grounding as everywhere) for real, and what keeps that from
costing more than the operator decided.

Nothing happens unless all of these are set: ``FITWITNESS_LIVE_DEMO=on``, ``ANTHROPIC_API_KEY``,
``FITWITNESS_ANTHROPIC_MODEL`` and the two prices (``FITWITNESS_ANTHROPIC_INPUT_USD_PER_MILLION`` / ``..._OUTPUT_...``),
a daily ceiling ``FITWITNESS_LIVE_DAILY_USD`` and a per-run ceiling ``FITWITNESS_LIVE_RUN_USD`` that is no more than
the daily one. Three limits stack:

* one run can never cost more than the per-run ceiling (the run's budget refuses a call it could not afford, before
  making it);
* each run reserves that whole ceiling against the day's ceiling in one atomic statement, before it is queued, so the
  day's spend cannot pass the ceiling however many requests arrive at once (a run that costs less leaves part of its
  reservation unused until midnight UTC: the limit errs toward stopping early);
* each workspace gets ``FITWITNESS_LIVE_PER_SESSION`` live runs (default 2), and a repeated idempotency key reserves
  nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import os


@dataclass(frozen=True)
class LiveConfig:
    enabled: bool = False
    model: str = ""
    run_usd: Decimal = Decimal(0)
    daily_usd: Decimal = Decimal(0)
    per_session: int = 2

    def remaining_runs(self, reserved: Decimal) -> int:
        if not self.enabled or self.run_usd <= 0:
            return 0
        return max(0, int((self.daily_usd - reserved) // self.run_usd))


def _money(value: str | None) -> Decimal:
    try:
        number = Decimal(value or "0")
    except InvalidOperation:
        return Decimal(0)
    return number if number.is_finite() and number > 0 else Decimal(0)


def load(env=None) -> LiveConfig:
    env = os.environ if env is None else env
    model = env.get("FITWITNESS_ANTHROPIC_MODEL", "")
    run_usd, daily_usd = _money(env.get("FITWITNESS_LIVE_RUN_USD")), _money(env.get("FITWITNESS_LIVE_DAILY_USD"))
    priced = _money(env.get("FITWITNESS_ANTHROPIC_INPUT_USD_PER_MILLION")) > 0 and _money(env.get("FITWITNESS_ANTHROPIC_OUTPUT_USD_PER_MILLION")) > 0
    try:
        per_session = max(1, min(10, int(env.get("FITWITNESS_LIVE_PER_SESSION", "2"))))
    except ValueError:
        per_session = 2
    on = (env.get("FITWITNESS_LIVE_DEMO") == "on" and bool(env.get("ANTHROPIC_API_KEY")) and bool(model) and priced
          and 0 < run_usd <= daily_usd)
    return LiveConfig(enabled=on, model=model, run_usd=run_usd, daily_usd=daily_usd, per_session=per_session)


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()
