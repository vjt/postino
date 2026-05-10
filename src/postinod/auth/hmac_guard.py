"""HMAC-SHA256 verification for the Zitadel Actions v2 webhook surface.

Zitadel signs the raw request body with a shared secret (configured at
Target-creation time). We compute the same digest with the postinod
secret, compare with hmac.compare_digest (constant-time), reject
mismatches with 401 in the calling handler (Task 9).

Header name: `ZITADEL-Signature` — verify exact spelling against the
running Zitadel version during e2e (Task 17 covers this).

The guard-style helper that runs as a Litestar Guard was dropped in
favour of inline verification in the events router (Task 9): Litestar
Guards operate on ASGIConnection, not Request, so reading the body
inside a Guard consumes the receive channel and breaks downstream
parsing. Inline verification reads the body once, verifies, then
parses — simpler, correct.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass


@dataclass(frozen=True)
class HmacVerifier:
    """Constant-time HMAC-SHA256 verifier.

    Stateless: instantiate once at app startup with the secret, share
    across requests. The header name is configurable so tests can drive
    canonical Zitadel headers without hardcoding.
    """

    secret: bytes
    header_name: str = "ZITADEL-Signature"
    # NOTE: Litestar (via Starlette) normalises HTTP header keys to lowercase
    # in `request.headers`. Callers must `.lower()` this value before
    # `request.headers.get(...)` lookup, or the lookup silently returns None
    # and the caller would auth-bypass on every request.

    def __repr__(self) -> str:
        return f"HmacVerifier(secret=****, header_name={self.header_name!r})"

    def verify(self, body: bytes, signature_hex: str) -> bool:
        """Return True iff signature_hex == HMAC-SHA256(secret, body)."""
        expected = hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature_hex)
