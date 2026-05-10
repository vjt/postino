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
from sqlalchemy.engine import Connection

from postino_core.enums import PasswordScheme
from postino_core.errors import ConfigError


class NoAuthProvider:
    """IdentityProvider that defers all credential ops to an external IdP."""

    def create_identity(
        self,
        conn: Connection,
        username: str,
        name: str,
        password: SecretStr | None,
        scheme: PasswordScheme | None,
    ) -> None:
        return None

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
