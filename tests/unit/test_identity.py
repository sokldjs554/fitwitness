"""ID tokens: what the verifier accepts and every way it refuses."""

import time

import pytest

from fitwitness.api import identity
from identity_helpers import FakeIssuer


@pytest.fixture
def issuer(tmp_path):
    fake = FakeIssuer()
    config = identity.IdentityConfig(issuer=fake.issuer, audience=fake.audience, jwks_file=fake.write_jwks(tmp_path / "jwks.json"))
    return fake, identity.Verifier(config)


@pytest.mark.parametrize("alg", ["RS256", "ES256"])
def test_a_token_the_issuer_signed_names_the_account_and_the_display_name(issuer, alg):
    fake, verifier = issuer
    person = verifier.verify(fake.token("u1", "김심사", alg=alg))
    assert person == identity.Identity(account=f"{fake.issuer}#u1", name="김심사")
    assert verifier.verify(fake.token("u2", None, alg=alg, preferred_username="park")).name == "park"
    assert verifier.verify(fake.token("u3", None, alg=alg)).name == "u3"


def test_every_other_token_is_refused(issuer):
    fake, verifier = issuer
    now = int(time.time())
    refused = {
        "no token": "",
        "not a token": "abc",
        "unsigned": fake.token(alg="none"),
        "another key, same kid": fake.token(key=fake.other),
        "unknown kid": fake.token(kid="rsa-9"),
        "wrong key for the algorithm": fake.token(alg="RS256", kid="ec-1"),
        "expired": fake.token(exp=now - 120),
        "not yet valid": fake.token(nbf=now + 120),
        "another issuer": fake.token(iss="https://other.test"),
        "another audience": fake.token(aud="someone-else"),
        "no subject": fake.token(sub=""),
    }
    for why, token in refused.items():
        with pytest.raises(identity.IdentityError):
            verifier.verify(token)
    assert verifier.verify(fake.token(exp=now - 30)).name  # inside the 60-second leeway
    assert verifier.verify(fake.token(aud=["x", fake.audience])).name  # an audience list that names this service


def test_the_bearer_header_must_be_present_and_well_formed():
    assert identity.bearer("Bearer abc.def.ghi") == "abc.def.ghi"
    for header in (None, "", "Basic abc", "Bearer", "Bearer  "):
        with pytest.raises(identity.IdentityError):
            identity.bearer(header)


def test_keys_are_fetched_once_and_again_only_for_a_key_not_seen(issuer, monkeypatch):
    fake, _ = issuer
    calls = []

    def fetch():
        calls.append(1)
        return fake.jwks()["keys"]

    verifier = identity.Verifier(identity.IdentityConfig(issuer=fake.issuer, audience=fake.audience), fetch=fetch)
    for _ in range(3):
        verifier.verify(fake.token())
    assert len(calls) == 1
    with pytest.raises(identity.IdentityError):
        verifier.verify(fake.token(kid="rsa-9"))  # unknown: one refetch, still unknown
    assert len(calls) == 1  # within a minute of the last fetch no refetch happens
    monkeypatch.setattr(verifier, "_fetched_at", verifier._fetched_at - 61)
    with pytest.raises(identity.IdentityError):
        verifier.verify(fake.token(kid="rsa-9"))
    assert len(calls) == 2


def test_nothing_is_configured_in_the_demo(monkeypatch):
    for name in ("FITWITNESS_OIDC_ISSUER", "FITWITNESS_OIDC_AUDIENCE", "FITWITNESS_OIDC_JWKS_FILE", "FITWITNESS_OIDC_JWKS_URL"):
        monkeypatch.delenv(name, raising=False)
    assert not identity.load().enabled
    monkeypatch.setenv("FITWITNESS_OIDC_ISSUER", "https://issuer.test")
    assert not identity.load().enabled, "an issuer without an audience is not a configuration"
