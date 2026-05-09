"""IdentityProvider Protocol — the seam between local and Zitadel modes."""
from __future__ import annotations

from typing import Protocol

from pydantic import EmailStr, SecretStr
from sqlalchemy.engine import Connection

from postino_core.enums import PasswordScheme


class IdentityProvider(Protocol):
    """Owner of authentication identity for a mailbox.

    LocalProvider stores credentials in PA mailbox.password (with
    {scheme} prefix). ZitadelProvider (V2) creates the identity in
    Zitadel and writes the {NOAUTH} sentinel to mailbox.password.
    """

    def create_identity(
        self,
        conn: Connection,
        username: EmailStr,
        name: str,
        password: SecretStr,
        scheme: PasswordScheme,
    ) -> None:
        """Establish the identity. Called immediately after the mailbox
        row INSERT (with sentinel password). LocalProvider UPDATEs the
        password column. Returns None; raises ConfigError or DBError."""
        ...

    def set_password(
        self,
        conn: Connection,
        username: EmailStr,
        password: SecretStr,
        scheme: PasswordScheme,
    ) -> None:
        """Change the password for an existing identity.

        Returns None on success.
        Raises NotFoundError if identity is absent; ConfigError if scheme
        is unsupported."""
        ...

    def delete_identity(
        self,
        conn: Connection,
        username: EmailStr,
    ) -> None:
        """Remove identity (idempotent — no error if absent).

        For LocalProvider this is a no-op (the mailbox row deletion in
        MailboxService.delete already drops the row's password)."""
        ...

    def supports_password_change(self) -> bool:
        """Whether `postino user passwd` is exposed.

        LocalProvider: True. ZitadelProvider: False."""
        ...
