"""HMAC-SHA256 verification for the Zitadel Actions v2 webhook surface.

Zitadel signs the raw request body with a shared secret (configured at
Target-creation time). We compute the same digest with the postinod
secret, compare with hmac.compare_digest (constant-time), reject
mismatches with 401 in the calling handler (Task 9).

Header name: `ZITADEL-Signature` — verify exact spelling against the
running Zitadel version during e2e (Task 17 covers this).

Rotation overlap: the verifier accepts a tuple of secrets and returns
True on the first match. During a key roll, publish two secrets to
Zitadel + postinod; once Zitadel cuts to the new one, drop the old
secret from `POSTINOD_ZITADEL_HMAC_SECRET`.

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

    Stateless: instantiate once at app startup with the secrets tuple,
    share across requests. The header name is configurable so tests can
    drive canonical Zitadel headers without hardcoding.
    """

    secrets: tuple[bytes, ...]
    header_name: str = "ZITADEL-Signature"

    def __post_init__(self) -> None:
        if not self.secrets:
            raise ValueError("HmacVerifier requires at least one secret")

    def __repr__(self) -> str:
        return f"HmacVerifier(secrets=****x{len(self.secrets)}, header_name={self.header_name!r})"

    def verify(self, body: bytes, signature_hex: str) -> bool:
        """Return True iff `signature_hex` matches HMAC-SHA256(secret, body)
        under any configured secret.

        Iterates the rotation set with `hmac.compare_digest` per secret so
        every comparison is constant-time. Worst case = len(secrets) digest
        computations; rotation overlaps are 1-2 entries, so the cost is
        negligible.
        """
        for secret in self.secrets:
            expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected, signature_hex):
                return True
        return False
