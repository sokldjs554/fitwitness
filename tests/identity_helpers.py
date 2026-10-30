"""A stand-in OpenID issuer for the tests: keys, a JWKS file, and ID tokens it signs. Nothing here is a real issuer."""

import base64
import json
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _uint(n: int, length: int | None = None) -> str:
    return _b64(n.to_bytes(length or (n.bit_length() + 7) // 8, "big"))


class FakeIssuer:
    def __init__(self, issuer="https://issuer.test", audience="fitwitness-test"):
        self.issuer, self.audience = issuer, audience
        self.rsa = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.ec = ec.generate_private_key(ec.SECP256R1())
        self.other = rsa.generate_private_key(public_exponent=65537, key_size=2048)  # a key the issuer does not publish

    def jwks(self) -> dict:
        r, e = self.rsa.public_key().public_numbers(), self.ec.public_key().public_numbers()
        return {
            "keys": [
                {"kty": "RSA", "kid": "rsa-1", "alg": "RS256", "use": "sig", "n": _uint(r.n), "e": _uint(r.e)},
                {"kty": "EC", "kid": "ec-1", "alg": "ES256", "crv": "P-256", "use": "sig", "x": _uint(e.x, 32), "y": _uint(e.y, 32)},
            ]
        }

    def write_jwks(self, path) -> str:
        path.write_text(json.dumps(self.jwks()))
        return str(path)

    def token(self, sub="u1", name="김심사", *, alg="RS256", kid=None, key=None, exp=None, nbf=None, iss=None, aud=None, **extra) -> str:
        header = {"alg": alg, "typ": "JWT", "kid": kid or ("rsa-1" if alg == "RS256" else "ec-1")}
        claims = {
            "iss": self.issuer if iss is None else iss,
            "aud": self.audience if aud is None else aud,
            "sub": sub,
            "exp": int(time.time()) + 600 if exp is None else exp,
            "iat": int(time.time()),
            **extra,
        }
        if name is not None:
            claims["name"] = name
        if nbf is not None:
            claims["nbf"] = nbf
        signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}"
        if alg == "RS256":
            signature = (key or self.rsa).sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
        elif alg == "ES256":
            r, s = utils.decode_dss_signature((key or self.ec).sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
            signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        else:  # "none" and friends: an unsigned token the verifier must refuse
            signature = b""
        return f"{signing_input}.{_b64(signature)}"
