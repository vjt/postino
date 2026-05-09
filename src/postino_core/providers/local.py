"""LocalProvider — keeps password in mailbox.password.

Participates in the caller's SQLAlchemy transaction (the `conn`
parameter is the Connection inside an outer `engine.begin()`)."""
from __future__ import annotations

from pydantic import EmailStr, SecretStr
from sqlalchemy import MetaData, update
from sqlalchemy.engine import Connection

from postino_core.enums import PasswordScheme
from postino_core.errors import NotFoundError
from postino_core.password import hash_password


class LocalProvider:
    """IdentityProvider implementation against the PA mailbox.password column."""

    def __init__(self, *, metadata: MetaData) -> None:
        self._metadata = metadata

    def create_identity(
        self,
        conn: Connection,
        username: EmailStr,
        name: str,  # noqa: ARG002 — Protocol contract
        password: SecretStr,
        scheme: PasswordScheme,
    ) -> None:
        """Replace the sentinel password set by MailboxService.add with a hashed one."""
        self._set(conn, username, password, scheme, must_exist=True)

    def set_password(
        self,
        conn: Connection,
        username: EmailStr,
        password: SecretStr,
        scheme: PasswordScheme,
    ) -> None:
        self._set(conn, username, password, scheme, must_exist=True)

    def delete_identity(
        self,
        conn: Connection,
        username: EmailStr,
    ) -> None:
        # No-op: the mailbox row deletion drops the password column with it.
        return None

    def supports_password_change(self) -> bool:
        return True

    def _set(
        self,
        conn: Connection,
        username: EmailStr,
        password: SecretStr,
        scheme: PasswordScheme,
        *,
        must_exist: bool,
    ) -> None:
        mailbox = self._metadata.tables["mailbox"]
        hashed = hash_password(password, scheme)
        result = conn.execute(
            update(mailbox)
            .where(mailbox.c.username == str(username))
            .values(password=hashed)
        )
        if must_exist and result.rowcount == 0:
            raise NotFoundError(f"mailbox {username} does not exist")
