"""The LangChain adapters build their structured-output chains for every schema the project sends, with no network.

test_provider_retry.py drives the retry logic with a hand-written client, so a change in how langchain_openai or
langchain_anthropic turns a pydantic schema into a request would pass CI and break the first paid run. These build the real
chat-model objects with a fake key (no request is made) and ask each for every schema's structured-output chain."""

import pytest

from fitwitness.agents.providers import ModelClient
from fitwitness.agents.tools import SearchPlan
from fitwitness.claims.audit import AuditVerdict
from fitwitness.claims.graph import ClaimDraft
from fitwitness.contracts import Budget
from fitwitness.retrieval.understanding import UnderstoodQuery

SCHEMAS = [SearchPlan, ClaimDraft, AuditVerdict, UnderstoodQuery]


def _client(provider: str, monkeypatch) -> ModelClient:
    prefix = "FITWITNESS_" + provider.upper()
    monkeypatch.setenv(provider.upper() + "_API_KEY", "fake-key-for-a-contract-test")
    monkeypatch.setenv(prefix + "_INPUT_USD_PER_MILLION", "1")
    monkeypatch.setenv(prefix + "_OUTPUT_USD_PER_MILLION", "5")
    return ModelClient(provider, "contract-test-model", _budget())


def _budget():
    from fitwitness.agents.budget import BudgetTracker

    return BudgetTracker(Budget())


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("schema", SCHEMAS, ids=lambda s: s.__name__)
def test_every_schema_becomes_a_structured_output_chain_for_both_providers(provider, schema, monkeypatch):
    client = _client(provider, monkeypatch).client
    chain = client.with_structured_output(schema, include_raw=True)
    assert hasattr(chain, "invoke")  # a runnable that would call the model, parse into the schema and keep the raw message


def test_a_provider_without_a_key_or_prices_is_refused_before_any_call(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        ModelClient("anthropic", "m", _budget())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.delenv("FITWITNESS_ANTHROPIC_INPUT_USD_PER_MILLION", raising=False)
    with pytest.raises(KeyError):  # no prices, no way to cap the spend: it does not start
        ModelClient("anthropic", "m", _budget())


def test_an_openai_reasoning_model_is_asked_for_minimal_reasoning_and_a_wider_limit_only_when_the_operator_asks(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    monkeypatch.setenv("FITWITNESS_OPENAI_INPUT_USD_PER_MILLION", "0.25")
    monkeypatch.setenv("FITWITNESS_OPENAI_OUTPUT_USD_PER_MILLION", "2")
    monkeypatch.delenv("FITWITNESS_OPENAI_REASONING_EFFORT", raising=False)
    minimal = ModelClient("openai", "gpt-5-mini", _budget())
    assert minimal.client.reasoning_effort == "minimal" and minimal.max_output == minimal.client.max_tokens == 1500
    plain = ModelClient("openai", "gpt-4.1-mini", _budget())
    assert plain.client.reasoning_effort is None and plain.max_output == 1500, "a model that does not reason is not sent the setting"
    monkeypatch.setenv("FITWITNESS_OPENAI_REASONING_EFFORT", "low")
    low = ModelClient("openai", "gpt-5-mini", _budget())
    assert low.client.reasoning_effort == "low" and low.max_output == low.client.max_tokens == 6000
    monkeypatch.setenv("FITWITNESS_OPENAI_REASONING_EFFORT", "extreme")
    with pytest.raises(ValueError):
        ModelClient("openai", "gpt-5-mini", _budget())


def test_the_model_verdict_schema_becomes_a_structured_output_chain_for_both_providers(monkeypatch):
    from fitwitness.evaluation.claims_model_decides import ModelVerdict

    for provider in ("anthropic", "openai"):
        assert hasattr(_client(provider, monkeypatch).client.with_structured_output(ModelVerdict, include_raw=True), "invoke")
