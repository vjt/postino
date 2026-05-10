"""MailboxService — creates, reads, updates, deletes mailbox rows.

The `add` method orders the steps so a process crash never leaves a
committed DB row pointing at a missing maildir:

1. Create the maildir on disk first.
2. Open the DB transaction (capacity checks, mailbox row, quota row,
   identity provider). On any failure here, the freshly-created
   maildir is removed; a pre-existing maildir is left alone (its
   owning row predates this call).
3. Run the postcreation hook. On hook failure: delete the row first,
   then attempt maildir cleanup. Each compensation step is wrapped so
   a secondary failure cannot mask the original HookError."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import EmailStr, SecretStr
from sqlalchemy import MetaData, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.row import RowMapping
from sqlalchemy.exc import IntegrityError

from postino_core.db import translate_db_errors
from postino_core.enums import MailboxStatus, PasswordScheme
from postino_core.errors import (
    AlreadyExistsError,
    CapacityError,
    DBError,
    NotFoundError,
)
from postino_core.fs import FilesystemAdapter
from postino_core.hooks import HookRunner
from postino_core.models import Mailbox, MailboxCreate
from postino_core.providers import SENTINEL_NOAUTH, IdentityProvider

_logger = logging.getLogger(__name__)


class MailboxService:
    def __init__(
        self,
        *,
        engine: Engine,
        identity: IdentityProvider,
        fs: FilesystemAdapter,
        hooks: HookRunner,
        clock: Callable[[], datetime],
        metadata: MetaData,
    ) -> None:
        self._engine = engine
        self._identity = identity
        self._fs = fs
        self._hooks = hooks
        self._clock = clock
        self._md = metadata

    def add(self, create: MailboxCreate) -> Mailbox:
        """Create a new mailbox.

        Returns: the parsed Mailbox row.
        Raises: NotFoundError, AlreadyExistsError, CapacityError,
                FilesystemError, HookError, DBError, DeadlockError.
        """
        local_part, _, domain = str(create.username).partition("@")
        relative_maildir = Path(domain) / local_part / ""

        # Track whether the maildir already existed so a DB-tx failure
        # does not delete a maildir owned by another (pre-existing) row.
        maildir_existed = self._fs.maildir_exists(relative_maildir)
        self._fs.create_maildir(relative_maildir)

        try:
            with translate_db_errors(), self._engine.begin() as conn:
                self._assert_domain_capacity(conn, domain)
                self._insert_mailbox_row(conn, create, local_part, domain, relative_maildir)
                self._insert_quota_row(conn, str(create.username))
                self._identity.create_identity(
                    conn,
                    str(create.username),
                    name=create.name,
                    password=create.password,
                    scheme=create.scheme,
                )
        except Exception:
            if not maildir_existed:
                self._safe_remove_maildir(relative_maildir)
            raise

        try:
            self._hooks.run_postcreation(str(create.username))
        except Exception:
            self._safe_delete_mailbox_row(str(create.username))
            self._safe_remove_maildir(relative_maildir)
            raise

        got = self.get(create.username)
        if got is None:
            raise DBError("mailbox vanished after insert")
        return got

    def _safe_remove_maildir(self, relative: Path) -> None:
        try:
            self._fs.remove_maildir(relative)
        except Exception as compensation_err:
            _logger.error(
                "compensation: remove_maildir(%s) failed: %s",
                relative,
                compensation_err,
            )

    def _safe_delete_mailbox_row(self, username: str) -> None:
        try:
            self._delete_mailbox_row(username)
        except Exception as compensation_err:
            _logger.error(
                "compensation: delete mailbox row %s failed: %s",
                username,
                compensation_err,
            )

    def get(self, username: EmailStr) -> Mailbox | None:
        """Return the mailbox or None if absent."""
        mailbox = self._md.tables["mailbox"]
        with self._engine.connect() as conn:
            row = conn.execute(
                select(mailbox).where(mailbox.c.username == str(username))
            ).fetchone()
        if row is None:
            return None
        return self._row_to_model(row._mapping)  # type: ignore[arg-type]

    def _assert_domain_capacity(self, conn: Connection, domain: str) -> None:
        d = self._md.tables["domain"]
        m = self._md.tables["mailbox"]
        row = conn.execute(
            select(d.c.mailboxes).where(d.c.domain == domain).with_for_update()
        ).fetchone()
        if row is None:
            raise NotFoundError(f"domain {domain!r} does not exist")
        cap = int(row[0])
        if cap > 0:
            count = conn.execute(
                select(func.count()).select_from(m).where(m.c.domain == domain)
            ).scalar_one()
            if count >= cap:
                raise CapacityError(f"domain {domain!r} reached max_mailboxes={cap}")

    def _insert_mailbox_row(
        self,
        conn: Connection,
        create: MailboxCreate,
        local_part: str,
        domain: str,
        maildir: Path,
    ) -> None:
        mailbox = self._md.tables["mailbox"]
        now = self._clock()
        try:
            conn.execute(
                mailbox.insert().values(
                    username=str(create.username),
                    password=SENTINEL_NOAUTH,
                    name=create.name,
                    maildir=str(maildir) + "/",
                    quota=create.quota_bytes,
                    local_part=local_part,
                    domain=domain,
                    active=int(MailboxStatus.ACTIVE),
                    created=now,
                    modified=now,
                )
            )
        except IntegrityError as e:
            raise AlreadyExistsError(f"mailbox {create.username} already exists") from e

    def _insert_quota_row(self, conn: Connection, username: str) -> None:
        """UPSERT a fresh quota2 row.

        Resets bytes/messages to 0 if a stale row survived a prior partial
        add — the round-trip via INSERT-then-IntegrityError-catch left
        whatever counters the orphan row had, which would mis-bill the
        new mailbox under its `username`."""
        quota2 = self._md.tables["quota2"]
        stmt = mysql_insert(quota2).values(username=username, bytes=0, messages=0)
        conn.execute(stmt.on_duplicate_key_update(bytes=0, messages=0))

    def _delete_mailbox_row(self, username: str) -> None:
        mailbox = self._md.tables["mailbox"]
        quota2 = self._md.tables["quota2"]
        with translate_db_errors(), self._engine.begin() as conn:
            conn.execute(quota2.delete().where(quota2.c.username == username))
            conn.execute(mailbox.delete().where(mailbox.c.username == username))

    def _row_to_model(self, m: RowMapping) -> Mailbox:
        return Mailbox(
            username=str(m["username"]),
            name=str(m["name"]),
            maildir=Path(str(m["maildir"])),
            quota_bytes=int(m["quota"]),  # type: ignore[arg-type]
            local_part=str(m["local_part"]),
            domain=str(m["domain"]),
            status=MailboxStatus(int(m["active"])),  # type: ignore[arg-type]
            created=m["created"],  # type: ignore[arg-type]
            modified=m["modified"],  # type: ignore[arg-type]
        )

    def delete(self, username: EmailStr, *, keep_maildir: bool) -> None:
        """Delete the mailbox row + quota row + (optionally) maildir.

        Idempotent on FS removal but DB row absence raises NotFoundError.

        Logs a WARNING for every alias whose `goto` references this
        username — the alias survives but now has a dead recipient. The
        admin chooses whether to clean up; spec policy is non-blocking."""
        mailbox = self._md.tables["mailbox"]
        existing = self.get(username)
        if existing is None:
            raise NotFoundError(f"mailbox {username} does not exist")
        relative = existing.maildir
        orphan_aliases = self._aliases_targeting(str(username))
        with translate_db_errors(), self._engine.begin() as conn:
            self._identity.delete_identity(conn, str(username))
            quota2 = self._md.tables["quota2"]
            conn.execute(quota2.delete().where(quota2.c.username == str(username)))
            conn.execute(mailbox.delete().where(mailbox.c.username == str(username)))
        if orphan_aliases:
            _logger.warning(
                "deleted %s; %d alias(es) still target it: %s",
                username,
                len(orphan_aliases),
                ", ".join(sorted(orphan_aliases)),
            )
        if not keep_maildir:
            self._fs.remove_maildir(relative)

    def _aliases_targeting(self, username: str) -> list[str]:
        """Aliases whose `goto` contains an exact (comma-split, trimmed)
        match for `username`. PA stores multi-recipient aliases as a
        comma-separated list — substring match would over-flag (e.g.
        `bob@example.com` would alarm on `bbob@example.com`)."""
        alias = self._md.tables["alias"]
        with self._engine.connect() as conn:
            candidates = conn.execute(
                select(alias.c.address, alias.c.goto).where(alias.c.goto.contains(username))
            ).fetchall()
        out: list[str] = []
        for r in candidates:
            address = str(r._mapping["address"])  # type: ignore[index]  # WHY: SQLAlchemy RowMapping[str, Any] indexing.
            goto = str(r._mapping["goto"])  # type: ignore[index]  # WHY: SQLAlchemy RowMapping[str, Any] indexing.
            if username in {part.strip() for part in goto.split(",")}:
                out.append(address)
        return out

    def list(
        self,
        *,
        domain: str | None,
        include_disabled: bool,
    ) -> list[Mailbox]:
        """List mailboxes, optionally scoped to a domain.

        Returns mailboxes ordered by username ascending."""
        mailbox = self._md.tables["mailbox"]
        stmt = select(mailbox).order_by(mailbox.c.username)
        if domain is not None:
            stmt = stmt.where(mailbox.c.domain == domain)
        if not include_disabled:
            stmt = stmt.where(mailbox.c.active == int(MailboxStatus.ACTIVE))
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [self._row_to_model(r._mapping) for r in rows]  # type: ignore[arg-type]

    def set_password(
        self,
        username: EmailStr,
        password: SecretStr,
        scheme: PasswordScheme,
    ) -> None:
        """Change password via the active IdentityProvider."""
        with translate_db_errors(), self._engine.begin() as conn:
            self._identity.set_password(conn, str(username), password, scheme)

    def set_name(self, username: EmailStr, name: str) -> None:
        """Update the mailbox display name."""
        mailbox = self._md.tables["mailbox"]
        now = self._clock()
        with translate_db_errors(), self._engine.begin() as conn:
            result = conn.execute(
                mailbox.update()
                .where(mailbox.c.username == str(username))
                .values(name=name, modified=now)
            )
            if result.rowcount == 0:
                raise NotFoundError(f"mailbox {username} does not exist")

    def set_status(self, username: EmailStr, status: MailboxStatus) -> None:
        """Enable / disable the mailbox."""
        mailbox = self._md.tables["mailbox"]
        now = self._clock()
        with translate_db_errors(), self._engine.begin() as conn:
            result = conn.execute(
                mailbox.update()
                .where(mailbox.c.username == str(username))
                .values(active=int(status), modified=now)
            )
            if result.rowcount == 0:
                raise NotFoundError(f"mailbox {username} does not exist")

    def set_quota(self, username: EmailStr, quota_bytes: int) -> None:
        """Set the per-mailbox quota cap."""
        if quota_bytes < 0:
            from postino_core.errors import ConfigError

            raise ConfigError("quota_bytes cannot be negative")
        mailbox = self._md.tables["mailbox"]
        now = self._clock()
        with translate_db_errors(), self._engine.begin() as conn:
            result = conn.execute(
                mailbox.update()
                .where(mailbox.c.username == str(username))
                .values(quota=quota_bytes, modified=now)
            )
            if result.rowcount == 0:
                raise NotFoundError(f"mailbox {username} does not exist")
