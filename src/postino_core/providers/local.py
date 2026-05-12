"""LocalProvider — keeps password in mailbox.password.

Participates in the caller's SQLAlchemy transaction (the `conn`
parameter is the Connection inside an outer `engine.begin()`)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from pydantic import SecretStr
from sqlalchemy import MetaData, select, update
from sqlalchemy.engine import Connection

from postino_core.enums import PasswordScheme
from postino_core.errors import ConfigError, NotFoundError
from postino_core.password import hash_password
from postino_core.providers.base import SENTINEL_NOAUTH


class LocalProvider:
    """IdentityProvider implementation against the PA mailbox.password column."""

    def __init__(self, *, metadata: MetaData, clock: Callable[[], datetime]) -> None:
        self._metadata = metadata
        self._clock = clock

    def create_identity(
        self,
        conn: Connection,
        username: str,
        name: str,
        password: SecretStr | None,
        scheme: PasswordScheme | None,
    ) -> None:
        """Replace the sentinel password set by MailboxService.add with a hashed one."""
        if password is None or scheme is None:
            raise ConfigError(
                "LOCAL identity backend requires both password and scheme to provision a mailbox"
            )
        self._set(conn, username, password, scheme, must_exist=True)

    def set_password(
        self,
        conn: Connection,
        username: str,
        password: SecretStr,
        scheme: PasswordScheme,
    ) -> None:
        self._set(conn, username, password, scheme, must_exist=True)

    def delete_identity(
        self,
        conn: Connection,
        username: str,
    ) -> None:
        # No-op: the mailbox row deletion drops the password column with it.
        return None

    def supports_password_change(self) -> bool:
        return True

    def supports_local_provisioning(self) -> bool:
        return True

    def release_identity(
        self,
        conn: Connection,
        username: str,
    ) -> None:
        del conn, username
        raise ConfigError(
            "local backend does not release to {NOAUTH}; switch to identity_backend=hybrid"
        )

    def supports_release_to_noauth(self) -> bool:
        return False

    def is_idp_managed(self, conn: Connection, username: str) -> bool:
        """Under the local backend every row carries a hash; rows on
        the sentinel exist only transiently between the bootstrap
        INSERT and ``create_identity``'s UPDATE inside the same tx.

        Returns False unconditionally for a known-existing row;
        raises ``NotFoundError`` if the row is absent."""
        mailbox = self._metadata.tables["mailbox"]
        row = conn.execute(
            select(mailbox.c.username).where(mailbox.c.username == username)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"mailbox {username} does not exist")
        return False

    def bootstrap_password_value(self) -> str:
        return SENTINEL_NOAUTH

    def _set(
        self,
        conn: Connection,
        username: str,
        password: SecretStr,
        scheme: PasswordScheme,
        *,
        must_exist: bool,
    ) -> None:
        mailbox = self._metadata.tables["mailbox"]
        hashed = hash_password(password, scheme)
        result = conn.execute(
            update(mailbox)
            .where(mailbox.c.username == username)
            .values(password=hashed, modified=self._clock())
        )
        if must_exist and result.rowcount == 0:
            raise NotFoundError(f"mailbox {username} does not exist")
