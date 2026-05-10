"""JWT bearer + JWKS verification for SCIM surface."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from postinod.auth.jwt_guard import JwtVerifier

ISSUER = "https://idp.example.org"
AUDIENCE = "postinod"
KID = "kid-test"


def _make_keypair() -> tuple[rsa.RSAPrivateKey, dict[str, str]]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_numbers = key.public_key().public_numbers()

    def _b64(i: int) -> str:
        b = i.to_bytes((i.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).decode().rstrip("=")

    jwk = {
        "kty": "RSA",
        "kid": KID,
        "use": "sig",
        "alg": "RS256",
        "n": _b64(pub_numbers.n),
        "e": _b64(pub_numbers.e),
    }
    return key, jwk


def _make_token(
    key: rsa.RSAPrivateKey,
    *,
    iss: str = ISSUER,
    aud: str = AUDIENCE,
    exp_offset: int = 3600,
) -> str:
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(
        {
            "iss": iss,
            "aud": aud,
            "exp": datetime.now(UTC) + timedelta(seconds=exp_offset),
            "sub": "alice@example.org",
        },
        pem,
        algorithm="RS256",
        headers={"kid": KID},
    )


class _StubJwksCache:
    def __init__(self, jwk: dict[str, str]) -> None:
        self._jwk = jwk

    async def get(self, kid: str) -> dict[str, object]:
        if kid != KID:
            raise KeyError(kid)
        return dict(self._jwk)


async def test_valid_token_passes() -> None:
    key, jwk = _make_keypair()
    token = _make_token(key)
    v = JwtVerifier(issuer=ISSUER, audience=AUDIENCE, jwks=_StubJwksCache(jwk))
    claims = await v.verify(token)
    assert claims["sub"] == "alice@example.org"


async def test_expired_token_rejected() -> None:
    key, jwk = _make_keypair()
    token = _make_token(key, exp_offset=-60)
    v = JwtVerifier(issuer=ISSUER, audience=AUDIENCE, jwks=_StubJwksCache(jwk))
    with pytest.raises(jwt.ExpiredSignatureError):
        await v.verify(token)


async def test_wrong_issuer_rejected() -> None:
    key, jwk = _make_keypair()
    token = _make_token(key, iss="https://attacker.example.org")
    v = JwtVerifier(issuer=ISSUER, audience=AUDIENCE, jwks=_StubJwksCache(jwk))
    with pytest.raises(jwt.InvalidIssuerError):
        await v.verify(token)


async def test_wrong_audience_rejected() -> None:
    key, jwk = _make_keypair()
    token = _make_token(key, aud="other-service")
    v = JwtVerifier(issuer=ISSUER, audience=AUDIENCE, jwks=_StubJwksCache(jwk))
    with pytest.raises(jwt.InvalidAudienceError):
        await v.verify(token)


async def test_unknown_kid_raises_key_error() -> None:
    key, jwk = _make_keypair()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": datetime.now(UTC) + timedelta(hours=1)},
        pem,
        algorithm="RS256",
        headers={"kid": "unknown-kid"},
    )
    v = JwtVerifier(issuer=ISSUER, audience=AUDIENCE, jwks=_StubJwksCache(jwk))
    with pytest.raises(KeyError):
        await v.verify(token)


async def test_missing_kid_rejected() -> None:
    key, jwk = _make_keypair()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": datetime.now(UTC) + timedelta(hours=1)},
        pem,
        algorithm="RS256",  # no kid in headers
    )
    v = JwtVerifier(issuer=ISSUER, audience=AUDIENCE, jwks=_StubJwksCache(jwk))
    with pytest.raises(jwt.InvalidTokenError):
        await v.verify(token)


async def test_malformed_token_rejected() -> None:
    _, jwk = _make_keypair()
    v = JwtVerifier(issuer=ISSUER, audience=AUDIENCE, jwks=_StubJwksCache(jwk))
    with pytest.raises(jwt.InvalidTokenError):
        await v.verify("not.a.valid.token")
