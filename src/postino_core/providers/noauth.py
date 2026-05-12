"""NoAuthProvider — for stacks where an external IdP owns authentication.

The mailbox row's ``password`` column carries the ``{NOAUTH}`` sentinel
indefinitely. Dovecot's passdb-sql sees the sentinel and falls through
to a sibling LDAP/OIDC passdb for actual authentication.

This provider deliberately refuses to mutate ``mailbox.password``:

* ``create_identity`` is a no-op (sentinel is already in place after the
  initial INSERT).
* ``set_password`` raises ``ConfigError`` — there is no local password
  to rotate; users change credentials in the IdP.
* ``delete_identity`` is a no-op (row deletion drops the column).
"""

from __future__ import annotations

from pydantic import SecretStr
from sqlalchemy import MetaData, select
from sqlalchemy.engine import Connection

from postino_core.enums import PasswordScheme
from postino_core.errors import ConfigError, NotFoundError
from postino_core.providers.base import SENTINEL_NOAUTH


class NoAuthProvider:
    """IdentityProvider that defers all credential ops to an external IdP."""

    def __init__(self, *, metadata: MetaData) -> None:
        self._metadata = metadata

    def create_identity(
        self,
        conn: Connection,
        username: str,
        name: str,
        password: SecretStr | None,
        scheme: PasswordScheme | None,
    ) -> None:
        del conn, username, name
        # Reject non-None password/scheme: the caller is asking for a
        # local password under a NoAuth backend, which would silently
        # discard the secret (the sentinel stays in mailbox.password).
        # Make it fail loudly so callers either gate via
        # supports_local_provisioning() or hand SecretStr(None).
        if password is not None or scheme is not None:
            raise ConfigError(
                "identity_backend=noauth: cannot accept password/scheme; "
                "provision credentials in the external IdP"
            )

    def set_password(
        self,
        conn: Connection,
        username: str,
        password: SecretStr,
        scheme: PasswordScheme,
    ) -> None:
        raise ConfigError(
            "identity_backend=noauth: password change must happen in the external IdP"
        )

    def delete_identity(
        self,
        conn: Connection,
        username: str,
    ) -> None:
        return None

    def supports_password_change(self) -> bool:
        return False

    def supports_local_provisioning(self) -> bool:
        return False

    def release_identity(
        self,
        conn: Connection,
        username: str,
    ) -> None:
        # Sentinel is already the permanent value; nothing to do.
        del conn, username
        return None

    def supports_release_to_noauth(self) -> bool:
        # Not meaningful under noauth (sentinel is always-on); refuse to
        # advertise the capability so the SCIM PATCH password handler
        # returns 403 mutability instead of silently no-op'ing.
        return False

    def is_idp_managed(self, conn: Connection, username: str) -> bool:
        """Every row under noauth is IdP-managed by definition.

        Row existence IS verified — uniform Protocol contract across
        Local / NoAuth / Hybrid so callers can rely on ``NotFoundError``
        for absent rows regardless of backend (A1-A6). The previous
        ``del conn, username; return True`` lied: callers gating on the
        predicate would have proceeded against a non-existent row."""
        mailbox = self._metadata.tables["mailbox"]
        row = conn.execute(
            select(mailbox.c.username).where(mailbox.c.username == username)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"mailbox {username} does not exist")
        return True

    def bootstrap_password_value(self) -> str:
        return SENTINEL_NOAUTH
