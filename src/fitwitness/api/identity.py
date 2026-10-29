"""Who the reviewer is: an OpenID Connect ID token, when the operator configured an issuer.

The public demo has no accounts. The reviewer is the name typed into the form, a second approval is a different name,
and the signed receipt proves what the server accepted, not who answered. With an issuer configured, every answer to a
parked run must carry ``Authorization: Bearer <ID token>``; the reviewer the run records, the receipt signs and the
second-approval rule compares is then the token's account (``iss`` and ``sub``), and the typed name is replaced by the
token's display name. The demo stays anonymous: nothing here runs unless all three settings are present.

* ``FITWITNESS_OIDC_ISSUER``: the ``iss`` a token must carry (the issuer's URL).
* ``FITWITNESS_OIDC_AUDIENCE``: the ``aud`` a token must carry (this deployment's client id).
* ``FITWITNESS_OIDC_JWKS_FILE`` or ``FITWITNESS_OIDC_JWKS_URL``: the issuer's signing keys; with neither, the keys are
  found through ``<issuer>/.well-known/openid-configuration`` on first use.

What is checked: the signature (RS256 or ES256, against the key the header's ``kid`` names), ``iss``, ``aud``,
``exp`` and ``nbf`` with 60 seconds of leeway, and that ``sub`` is present. An unknown ``kid`` refetches the keys, at
most once a minute. Nothing else about the token is trusted: roles and permissions are not read from it.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
import time

LEEWAY_SECONDS = 60
ALGORITHMS = {"RS256", "ES256"}


class IdentityError(ValueError):
    """A token that does not prove who is answering. The API reports it as 401."""


@dataclass(frozen=True)
class Identity:
    account: str  # "<iss>#<sub>": the stable name of the account across tokens
    name: str  # what the run shows as the reviewer: name, preferred_username or email, else the subject


@dataclass
class IdentityConfig:
    issuer: str = ""
    audience: str = ""
    jwks_file: str = ""
    jwks_url: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.issuer and self.audience)


def load() -> IdentityConfig:
    return IdentityConfig(issuer=os.getenv("FITWITNESS_OIDC_ISSUER", "").strip(), audience=os.getenv("FITWITNESS_OIDC_AUDIENCE", "").strip(),
                          jwks_file=os.getenv("FITWITNESS_OIDC_JWKS_FILE", "").strip(), jwks_url=os.getenv("FITWITNESS_OIDC_JWKS_URL", "").strip())


def _b64(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _uint(segment: str) -> int:
    return int.from_bytes(_b64(segment), "big")


def public_key(jwk: dict):
    """The verifying key a JWK describes (RSA or P-256), from the ``cryptography`` package the project already carries."""
    from cryptography.hazmat.primitives.asymmetric import ec, rsa

    if jwk.get("kty") == "RSA":
        return rsa.RSAPublicNumbers(_uint(jwk["e"]), _uint(jwk["n"])).public_key()
    if jwk.get("kty") == "EC" and jwk.get("crv") == "P-256":
        return ec.EllipticCurvePublicNumbers(_uint(jwk["x"]), _uint(jwk["y"]), ec.SECP256R1()).public_key()
    raise IdentityError("unsupported key type")


def _verify_signature(alg: str, key, signing_input: bytes, signature: bytes) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils

    try:
        if alg == "RS256":
            if not isinstance(key, rsa.RSAPublicKey):
                raise IdentityError("the key does not match the algorithm")
            key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
        else:
            if not isinstance(key, ec.EllipticCurvePublicKey) or len(signature) != 64:
                raise IdentityError("the key does not match the algorithm")
            der = utils.encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big"))
            key.verify(der, signing_input, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        raise IdentityError("the token's signature does not verify") from None


class Verifier:
    """Verifies ID tokens against the issuer's keys, which it fetches once and again when a token names a key it has not seen."""

    def __init__(self, config: IdentityConfig, fetch=None):
        self.config = config
        self._fetch = fetch or self._fetch_default
        self._keys: dict[str, dict] = {}
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    def _fetch_default(self) -> list[dict]:
        if self.config.jwks_file:
            return json.loads(Path(self.config.jwks_file).read_text())["keys"]
        import httpx

        url = self.config.jwks_url
        if not url:
            url = httpx.get(self.config.issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=5).raise_for_status().json()["jwks_uri"]
        return httpx.get(url, timeout=5).raise_for_status().json()["keys"]

    def key(self, kid: str) -> dict:
        with self._lock:
            if kid not in self._keys and time.monotonic() - self._fetched_at > 60 or not self._keys:
                self._keys = {k.get("kid", ""): k for k in self._fetch()}
                self._fetched_at = time.monotonic()
            if kid not in self._keys:
                raise IdentityError("the token names a signing key the issuer does not publish")
            return self._keys[kid]

    def verify(self, token: str, *, now: float | None = None) -> Identity:
        try:
            head, body, sig = token.strip().split(".")
            header = json.loads(_b64(head))
            claims = json.loads(_b64(body))
            signature = _b64(sig)
        except (ValueError, json.JSONDecodeError):
            raise IdentityError("not an ID token") from None
        alg = header.get("alg")
        if alg not in ALGORITHMS:
            raise IdentityError("the token's algorithm is not accepted")
        _verify_signature(alg, public_key(self.key(str(header.get("kid", "")))), f"{head}.{body}".encode(), signature)
        moment = time.time() if now is None else now
        if claims.get("iss") != self.config.issuer:
            raise IdentityError("the token was not issued by the configured issuer")
        audience = claims.get("aud")
        if self.config.audience not in (audience if isinstance(audience, list) else [audience]):
            raise IdentityError("the token was not issued for this service")
        if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] + LEEWAY_SECONDS < moment:
            raise IdentityError("the token has expired")
        if isinstance(claims.get("nbf"), (int, float)) and claims["nbf"] - LEEWAY_SECONDS > moment:
            raise IdentityError("the token is not valid yet")
        subject = str(claims.get("sub") or "").strip()
        if not subject:
            raise IdentityError("the token names no subject")
        name = next((str(claims[k]).strip() for k in ("name", "preferred_username", "email") if str(claims.get(k) or "").strip()), subject)
        return Identity(account=f"{self.config.issuer}#{subject}", name=name[:100])


def bearer(authorization: str | None) -> str:
    """The token in an ``Authorization: Bearer`` header, or an IdentityError saying one is required."""
    parts = (authorization or "").split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise IdentityError("담당자 답변에는 로그인 토큰(Authorization: Bearer)이 필요합니다")
    return parts[1].strip()
