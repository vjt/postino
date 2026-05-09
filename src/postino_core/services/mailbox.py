"""MailboxService — creates, reads, updates, deletes mailbox rows.

The `add` method runs the full create flow inside a single transaction
plus an outer try/except that cleans up the maildir if the post-DB
filesystem or hook step fails."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import EmailStr
from sqlalchemy import MetaData, func, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.row import RowMapping
from sqlalchemy.exc import IntegrityError

from postino_core.enums import MailboxStatus
from postino_core.errors import (
    AlreadyExistsError,
    CapacityError,
    DBError,
    FilesystemError,
    HookError,
    NotFoundError,
)
from postino_core.fs import FilesystemAdapter
from postino_core.hooks import HookRunner
from postino_core.models import Mailbox, MailboxCreate
from postino_core.providers.base import IdentityProvider

# {NOAUTH} sentinel — see spec §5. Used as initial value for password
# column (NOT NULL) before LocalProvider replaces it with a hashed value.
_SENTINEL = "{NOAUTH}"


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
                FilesystemError, HookError, DBError.
        """
        local_part, _, domain = str(create.username).partition("@")
        relative_maildir = Path(domain) / local_part / ""

        try:
            with self._engine.begin() as conn:
                self._assert_domain_capacity(conn, domain)
                self._insert_mailbox_row(conn, create, local_part, domain, relative_maildir)
                self._insert_quota_row(conn, str(create.username))
                self._identity.create_identity(
                    conn,
                    create.username,
                    name=create.name,
                    password=create.password,
                    scheme=create.scheme,
                )
            self._fs.create_maildir(relative_maildir)
            try:
                self._hooks.run_postcreation(str(create.username))
            except HookError:
                self._fs.remove_maildir(relative_maildir)
                self._delete_mailbox_row(str(create.username))
                raise
        except FilesystemError:
            self._delete_mailbox_row(str(create.username))
            raise

        got = self.get(create.username)
        if got is None:
            raise DBError("mailbox vanished after insert")
        return got

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
                raise CapacityError(
                    f"domain {domain!r} reached max_mailboxes={cap}"
                )

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
            conn.execute(mailbox.insert().values(
                username=str(create.username),
                password=_SENTINEL,
                name=create.name,
                maildir=str(maildir) + "/",
                quota=create.quota_bytes,
                local_part=local_part,
                domain=domain,
                active=int(MailboxStatus.ACTIVE),
                created=now,
                modified=now,
            ))
        except IntegrityError as e:
            raise AlreadyExistsError(
                f"mailbox {create.username} already exists"
            ) from e

    def _insert_quota_row(self, conn: Connection, username: str) -> None:
        quota2 = self._md.tables["quota2"]
        try:
            conn.execute(quota2.insert().values(
                username=username,
                bytes=0,
                messages=0,
            ))
        except IntegrityError:
            # quota2 may already have a row from a prior partial run; ignore.
            return None

    def _delete_mailbox_row(self, username: str) -> None:
        mailbox = self._md.tables["mailbox"]
        quota2 = self._md.tables["quota2"]
        with self._engine.begin() as conn:
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
