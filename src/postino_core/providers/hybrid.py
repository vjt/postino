"""HybridProvider — per-row credential ownership.

Rows with the ``{NOAUTH}`` sentinel are resolved by an external IdP
passdb (e.g. LDAP/OIDC) chained behind dovecot's passdb-sql. Rows with
a real hash authenticate via passdb-sql directly. The provider can
transition a row between the two states; each transition emits a
warning + the caller writes an audit row.

Participates in the caller's SQLAlchemy transaction (the `conn`
parameter is the Connection inside an outer `engine.begin()`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from pydantic import SecretStr
from sqlalchemy import MetaData, select, update
from sqlalchemy.engine import Connection

from postino_core.enums import PasswordScheme
from postino_core.errors import NotFoundError
from postino_core.password import hash_password
from postino_core.providers.base import SENTINEL_NOAUTH

_logger = logging.getLogger(__name__)


class HybridProvider:
    """IdentityProvider that supports both SQL-auth and IdP-auth on a per-row basis."""

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
        del name
        if password is None or scheme is None:
            # Leave the sentinel that _insert_mailbox_row already wrote.
            return
        self._write_hash(conn, username, password, scheme)

    def set_password(
        self,
        conn: Connection,
        username: str,
        password: SecretStr,
        scheme: PasswordScheme,
    ) -> None:
        current = self._current_password(conn, username)
        if current is None:
            raise NotFoundError(f"mailbox {username} does not exist")
        if current == SENTINEL_NOAUTH:
            _logger.warning(
                "user %s claimed into SQL auth (was {NOAUTH})",
                username,
            )
        self._write_hash(conn, username, password, scheme)

    def release_identity(
        self,
        conn: Connection,
        username: str,
    ) -> None:
        current = self._current_password(conn, username)
        if current is None:
            raise NotFoundError(f"mailbox {username} does not exist")
        if current == SENTINEL_NOAUTH:
            return
        _logger.warning(
            "user %s released to IdP auth (was SQL-authed)",
            username,
        )
        mailbox = self._metadata.tables["mailbox"]
        conn.execute(
            update(mailbox)
            .where(mailbox.c.username == username)
            .values(password=SENTINEL_NOAUTH, modified=self._clock())
        )

    def delete_identity(
        self,
        conn: Connection,
        username: str,
    ) -> None:
        del conn, username
        return None

    def supports_password_change(self) -> bool:
        return True

    def supports_local_provisioning(self) -> bool:
        return True

    def supports_release_to_noauth(self) -> bool:
        return True

    def is_idp_managed(self, conn: Connection, username: str) -> bool:
        """Reads ``mailbox.password`` and compares against the
        ``{NOAUTH}`` sentinel. The column itself stays private to the
        provider; ``MailboxService`` consumes only the predicate.

        Only the literal sentinel counts as IdP-managed. Empty strings
        used to count too — that semantic disagreed with LocalProvider
        (always False) and let two providers reach different
        conclusions on the same DB state. Tightened to a single-source
        sentinel literal (A1-A6); legacy empty-string rows are
        operator-actionable via `postino check --deep`."""
        current = self._current_password(conn, username)
        if current is None:
            raise NotFoundError(f"mailbox {username} does not exist")
        return current == SENTINEL_NOAUTH

    def bootstrap_password_value(self) -> str:
        return SENTINEL_NOAUTH

    def _current_password(self, conn: Connection, username: str) -> str | None:
        mailbox = self._metadata.tables["mailbox"]
        return conn.execute(
            select(mailbox.c.password).where(mailbox.c.username == username)
        ).scalar_one_or_none()

    def _write_hash(
        self,
        conn: Connection,
        username: str,
        password: SecretStr,
        scheme: PasswordScheme,
    ) -> None:
        mailbox = self._metadata.tables["mailbox"]
        hashed = hash_password(password, scheme)
        result = conn.execute(
            update(mailbox)
            .where(mailbox.c.username == username)
            .values(password=hashed, modified=self._clock())
        )
        if result.rowcount == 0:
            raise NotFoundError(f"mailbox {username} does not exist")
