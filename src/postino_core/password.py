"""Password hashing wrappers around passlib.

Hashes are stored in dovecot's {scheme}hash form so dovecot's SQL
passdb can pick the verifier per row. The {scheme} prefix MUST always
be present on rows postino writes — dovecot's default_pass_scheme only
covers legacy unprefixed rows."""

from __future__ import annotations

from typing import Any, cast

from passlib.hash import bcrypt, md5_crypt, sha512_crypt  # type: ignore[attr-defined]
from pydantic import SecretStr

from postino_core.enums import PasswordScheme
from postino_core.errors import ConfigError

_VERIFIERS: dict[PasswordScheme, Any] = {
    PasswordScheme.MD5_CRYPT: md5_crypt,
    PasswordScheme.BCRYPT: bcrypt,
    PasswordScheme.SHA512_CRYPT: sha512_crypt,
}


def hash_password(password: SecretStr, scheme: PasswordScheme) -> str:
    """Produce a dovecot-compatible '{SCHEME}hash' string.

    Returns: the prefixed hash.
    Raises: ConfigError if the scheme has no registered verifier
            (defensive — should not happen given the enum bound).
    """
    verifier = _VERIFIERS.get(scheme)
    if verifier is None:
        raise ConfigError(f"no verifier for scheme {scheme}")
    return f"{{{scheme.value}}}{cast(str, verifier.hash(password.get_secret_value()))}"


def verify_password(password: SecretStr, stored: str) -> bool:
    """Check a password against a {scheme}hash row value.

    Returns: True iff the password matches.
    Raises: ConfigError if `stored` is missing the {scheme} prefix or
            references an unknown scheme.
    """
    if not stored.startswith("{") or "}" not in stored:
        raise ConfigError("stored password missing {SCHEME} prefix")
    scheme_name, _, hashed = stored[1:].partition("}")
    try:
        scheme = PasswordScheme(scheme_name)
    except ValueError as e:
        raise ConfigError(f"unknown password scheme: {scheme_name!r}") from e
    verifier = _VERIFIERS.get(scheme)
    if verifier is None:
        raise ConfigError(f"no verifier for scheme {scheme}")
    try:
        return cast(bool, verifier.verify(password.get_secret_value(), hashed))
    except (ValueError, TypeError) as e:
        # passlib raises ValueError on malformed/truncated hash bytes
        # (e.g. a corrupted bcrypt row); surface as ConfigError so the
        # caller (CLI / SCIM) maps to a clean exit code instead of an
        # uncaught passlib internal (L1-S36).
        raise ConfigError(f"stored hash unverifiable under scheme {scheme.value}: {e}") from e
