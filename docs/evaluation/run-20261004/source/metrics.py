"""Failure-inclusive metrics; confidence intervals resample design families."""
import json
import random
from collections import defaultdict
from statistics import mean
from pydantic import BaseModel, ConfigDict
from typing import Literal


class Prediction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    verdict: Literal['match', 'mismatch', 'unknown']
    citations: list[str]


def model_payload(case):
    return {k: case[k] for k in ('query', 'requirements', 'facts')}


def parse_prediction(text, allowed):
    value = Prediction.model_validate_json(text).model_dump()
    if not set(value['citations']).issubset(allowed):
        raise ValueError('citation does not exist in source evidence')
    if value['verdict'] != 'unknown' and not value['citations']:
        raise ValueError('conclusive verdict requires source citation')
    return value


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * fraction
    low = int(index)
    return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (index - low)


def summarize(rows):
    n = len(rows)
    correct = [r['status'] == 'ok' and r['predicted'] == r['expected'] for r in rows]
    negative = [r for r in rows if r['expected'] != 'match']
    false_accepts = sum(r['predicted'] == 'match' for r in negative)
    families = defaultdict(list)
    for r, c in zip(rows, correct):
        families[r['family_id']].append(c)
    rng = random.Random(1701)
    keys = sorted(families)
    samples = []
    if keys:
        for _ in range(1000):
            sample = [c for key in rng.choices(keys, k=len(keys)) for c in families[key]]
            samples.append(mean(sample))
    citations = [c in r['valid_citations'] for r in rows for c in r.get('citations', [])]
    costs = [r.get('cost_usd') for r in rows]
    return dict(attempts=n, completed=sum(r['status']=='ok' for r in rows),
                accuracy=mean(correct) if n else None,
                accuracy_ci95=[percentile(samples,.025),percentile(samples,.975)],
                false_acceptances=false_accepts, nonmatching_cases=len(negative),
                false_acceptance_rate=false_accepts/len(negative) if negative else None,
                citation_validity=mean(citations) if citations else None,
                abstention_rate=sum(r['predicted']=='unknown' for r in rows)/n if n else None,
                latency_p50_ms=percentile([r['latency_ms'] for r in rows],.5),
                latency_p95_ms=percentile([r['latency_ms'] for r in rows],.95),
                input_tokens=sum(r['input_tokens'] for r in rows),
                output_tokens=sum(r['output_tokens'] for r in rows),
                cost_usd=sum(costs) if costs and all(c is not None for c in costs) else None)
