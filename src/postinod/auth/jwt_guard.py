"""JWT bearer verification for the SCIM surface.

Validates the JWT against (iss, aud, exp, signature) using JWKS-fetched
RSA keys. JWKS unknown-kid handling and stale-cache-on-failure live in
JwksCache; this module just routes the kid → key lookup and lets pyjwt
do the algorithm-specific verification.

Algorithm pinned to RS256 — Zitadel and most enterprise IdPs default
to it. If a future deployment needs ES256 / EdDSA, add it to the
algorithms list explicitly; do NOT accept algorithms from the token
header (the `alg=none` and algorithm-confusion attacks live there).

The Litestar Guard pattern was dropped here for symmetry with
HmacVerifier (Task 4): Task 12's SCIM Users router will call
JwtVerifier.verify(token) inline before parsing the SCIM payload.
"""

from __future__ import annotations

import time
from typing import Protocol

import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt.algorithms import RSAAlgorithm

# Default upper bound on accepted token age, in seconds. Bearer JWT
# lifetime is the IdP's call (often hours-to-days), but postinod
# narrows revocation latency by refusing tokens whose `iat` is older
# than this. Combined with JWKS rotation (scim_jwks_refresh_seconds)
# this caps the worst-case window for a leaked key.
DEFAULT_MAX_TOKEN_AGE_SECONDS = 3600


class JwksLike(Protocol):
    """Minimal Protocol satisfied by JwksCache (and the test stub).

    Defined locally rather than importing JwksCache so this module can
    be used with arbitrary key sources (e.g. a static-keys map for
    integration tests without an HTTP fixture).
    """

    async def get(self, kid: str) -> dict[str, object]: ...


class JwtVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks: JwksLike,
        max_token_age_seconds: int = DEFAULT_MAX_TOKEN_AGE_SECONDS,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks = jwks
        self._max_age = max_token_age_seconds

    async def verify(self, token: str) -> dict[str, object]:
        """Verify a bearer JWT. Returns the decoded claims dict.

        Raises:
            jwt.InvalidTokenError (and subclasses ExpiredSignatureError,
                InvalidIssuerError, InvalidAudienceError,
                MissingRequiredClaimError) on verification failure.
            jwt.InvalidKeyError if the JWKS entry is a private key rather
                than a public key.
            KeyError if the token's kid is unknown to the JWKS cache
                even after a forced refresh (caller maps to 401).
        """
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        if not kid:
            raise jwt.InvalidTokenError("token missing kid header")
        jwk = await self._jwks.get(kid)
        raw_key = RSAAlgorithm.from_jwk(jwk)
        # from_jwk returns AllowedRSAKeys = RSAPrivateKey | RSAPublicKey.
        # A public-only JWK (no "d" field) always yields RSAPublicKey; reject
        # private keys explicitly — assert strips under python -O and would
        # propagate a private key to jwt.decode with a cryptic error.
        if not isinstance(raw_key, RSAPublicKey):
            raise jwt.InvalidKeyError("JWKS entry is a private key — public-only JWKs required")
        decoded: dict[str, object] = jwt.decode(
            token,
            key=raw_key,
            algorithms=["RS256"],
            audience=self._audience,
            issuer=self._issuer,
            options={"require": ["exp", "iat"]},
        )  # type: ignore[assignment]  # WHY: pyjwt.decode returns dict[str, Any]; narrowed to dict[str, object]
        # Defence-in-depth: cap accepted token age against `iat` so
        # postinod is not at the IdP's mercy for revocation latency on
        # long-lived (e.g. 10y) JWTs (A4-A4.5).
        iat = decoded.get("iat")
        if not isinstance(iat, (int, float)):
            raise jwt.InvalidTokenError("token missing/invalid iat claim")
        age = time.time() - float(iat)
        if age > self._max_age:
            raise jwt.ExpiredSignatureError(f"token age {int(age)}s exceeds max {self._max_age}s")
        return decoded
