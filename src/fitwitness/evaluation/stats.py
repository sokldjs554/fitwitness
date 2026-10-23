"""How wide the committed numbers are: Wilson intervals, standard errors, pass^k over repeats, case bootstraps.

Nothing here changes a measurement. The records under docs/evaluation keep their counts; this module says
what a count of that size can and cannot distinguish."""
from math import sqrt
from statistics import mean, stdev
import random

Z95 = 1.959964


def wilson(correct: int, total: int, z: float = Z95) -> list[float] | None:
    """Wilson score interval for a proportion; well behaved at 0/n and n/n, where a normal interval collapses."""
    if total <= 0:
        return None
    p = correct / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def sem(values: list[float]) -> float | None:
    """Standard error of the mean of independent values."""
    if len(values) < 2:
        return None
    return round(stdev(values) / sqrt(len(values)), 4)


def pass_power_k(outcomes_by_case: dict[str, list[bool]]) -> dict:
    """pass^k: the share of cases correct on every one of their k repeats, beside the mean single-try accuracy.

    Repeats of one case are not independent trials, so 72 verdicts over 24 cases are 24 observations of
    reliability, not 72 of accuracy."""
    cases = {c: v for c, v in outcomes_by_case.items() if v}
    if not cases:
        return {"cases": 0}
    k = min(len(v) for v in cases.values())
    every = sum(all(v[:k]) for v in cases.values())
    return {"cases": len(cases), "k": k, "pass_power_k": round(every / len(cases), 4), "pass_power_k_ci95": wilson(every, len(cases)),
            "pass_at_1": round(mean(mean(v) for v in cases.values()), 4), "cases_all_correct": every}


def case_bootstrap(values_by_case: dict[str, list[float]], draws: int = 1000, seed: int = 1701) -> list[float] | None:
    """95% interval of the mean, resampling cases (each case brings all its repeats), not single rows."""
    keys = sorted(k for k, v in values_by_case.items() if v)
    if not keys:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        sample = [x for key in rng.choices(keys, k=len(keys)) for x in values_by_case[key]]
        means.append(mean(sample))
    means.sort()
    return [round(means[int(0.025 * (draws - 1))], 4), round(means[int(0.975 * (draws - 1))], 4)]
