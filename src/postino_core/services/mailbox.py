"""MailboxService — creates, reads, updates, deletes mailbox rows.

`add` ordering: the per-domain row is locked FOR UPDATE first so a
concurrent ``domain delete --force`` blocks on the lock; the maildir
is then created on disk *inside* the same transaction. On any failure
the DB tx rolls back FIRST (via the surrounding ``engine.begin()``
context manager), then the FS compensation runs in an outer
try/except so a deadlock during rollback cannot leave a row
referencing a deleted maildir.

The postcreation hook runs after commit because it may produce side
effects outside the maildir that postino cannot atomicize. On hook
failure the row is deleted (with bounded retry on deadlock) and the
maildir is removed. If either compensation step fails, a side-channel
``mailbox.create_rollback_failed`` audit row records the orphan
resources so `postino check --deep` and the operator have a durable
trail. Note: the postcreation hook itself is NOT re-runnable — a
hook-side side effect (sieve generation, external IMAP profile)
performed before its failure cannot be undone by postino. Operators
must reconcile manually via the audit row; a re-runnable hook
contract is a v0.7 follow-up requiring a `hook_state` schema column.

`delete` ordering: two-phase. An atomic ``os.rename`` to
``.deleting.<token>`` rides the DB tx; the full ``rmtree`` runs
post-commit. Rmtree failure leaves a graveyard for `postino check
--deep` to sweep rather than risking a partially-wiped maildir
restored under a live DB row (the prior contract's data-loss
window)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import EmailStr, SecretStr
from sqlalchemy import MetaData, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.row import RowMapping
from sqlalchemy.exc import IntegrityError

from postino_core.audit import AuditWriter, DefaultAuditWriter, mk_action, sanitize_audit_error
from postino_core.db import translate_db_errors
from postino_core.enums import MailboxStatus, PasswordScheme
from postino_core.errors import (
    AlreadyExistsError,
    CapacityError,
    DBError,
    DeadlockError,
    NotFoundError,
)
from postino_core.fs import FilesystemAdapter
from postino_core.hooks import HookRunner
from postino_core.models import Mailbox, MailboxCreate
from postino_core.providers import IdentityProvider

_logger = logging.getLogger(__name__)

_ROW_DELETE_RETRY_ATTEMPTS = 3
_ROW_DELETE_RETRY_DELAY_SECONDS = 0.2


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
        audit_writer: AuditWriter | None = None,
    ) -> None:
        self._engine = engine
        self._identity = identity
        self._fs = fs
        self._hooks = hooks
        self._clock = clock
        self._md = metadata
        self._audit: AuditWriter = audit_writer or DefaultAuditWriter(
            metadata=metadata, clock=clock
        )

    @property
    def identity(self) -> IdentityProvider:
        """Active IdentityProvider (read-only access).

        Exposed so callers (SCIM handlers, CLI commands) can branch on
        ``supports_password_change()`` / ``supports_local_provisioning()``
        / ``supports_release_to_noauth()`` before invoking mutators that
        would otherwise raise ConfigError mid-transaction."""
        return self._identity

    def add(self, create: MailboxCreate) -> Mailbox:
        """Create a new mailbox.

        Returns: the parsed Mailbox row.
        Raises: NotFoundError, AlreadyExistsError, CapacityError,
                FilesystemError, HookError, DBError, DeadlockError.
        """
        local_part, _, domain = str(create.username).partition("@")
        relative_maildir = Path(domain) / local_part / ""

        # Pre-existence flag: a pre-existing maildir survives a failed
        # add (legitimate when an operator pre-created the tree via
        # backup restore). A WARNING surfaces the orphan-suspect case
        # so it can be investigated even when the add succeeds.
        maildir_existed = self._fs.maildir_exists(relative_maildir)
        if maildir_existed:
            _logger.warning(
                "mailbox %s: maildir %s already exists; provisioning over "
                "existing tree — investigate prior tenant residue",
                create.username,
                relative_maildir,
            )

        # Track whether THIS call created the maildir so FS compensation
        # runs only when we own it (an operator-restored maildir survives
        # an add failure). Compensation runs OUTSIDE the `engine.begin()`
        # block so DB rollback completes first — otherwise a deadlock
        # during rollback could leave a half-committed row pointing at
        # a deleted maildir (L1-S44).
        maildir_created_by_us = False
        try:
            with translate_db_errors(), self._engine.begin() as conn:
                # Capacity check first: locks the domain row FOR UPDATE so a
                # concurrent ``domain delete --force`` blocks here and cannot
                # race the maildir create on disk.
                self._assert_domain_capacity(conn, domain)
                # Maildir create inside the tx: if any later step fails we
                # rollback the FS op alongside the DB op (skip rollback when
                # the maildir pre-existed — it isn't ours to delete).
                self._fs.create_maildir(relative_maildir)
                if not maildir_existed:
                    maildir_created_by_us = True
                self._insert_mailbox_row(conn, create, local_part, domain, relative_maildir)
                self._insert_quota_row(conn, str(create.username))
                self._identity.create_identity(
                    conn,
                    str(create.username),
                    name=create.name,
                    password=create.password,
                    scheme=create.scheme,
                )
                self._audit.write(
                    conn,
                    action=mk_action("mailbox", "create"),
                    domain=domain,
                    data=str(create.username),
                )
        except Exception:
            # DB tx rolled back by `engine.begin()` before we reach
            # here. Now compensate the FS op — order matters: DB
            # first, FS second, so a rollback failure does not leave a
            # phantom row pointing at a deleted maildir.
            if maildir_created_by_us:
                self._compensate_remove_maildir(relative_maildir)
            raise

        try:
            self._hooks.run_postcreation(
                username=str(create.username),
                domain=domain,
                maildir=str(relative_maildir),
                quota=create.quota_bytes,
            )
        except Exception:
            # The DB tx already committed (audit row + mailbox row + quota
            # row + identity bootstrap). Compensation deletes the row and
            # removes the maildir; collect any compensation failures and
            # surface them via a side-channel audit row so the operator
            # sees a durable record of the orphan resources rather than
            # just a log line.
            orphans: list[str] = []
            row_err = self._compensate_delete_mailbox_row(str(create.username))
            if row_err is not None:
                orphans.append(f"mailbox row ({row_err})")
            fs_err = self._compensate_remove_maildir(relative_maildir)
            if fs_err is not None:
                orphans.append(f"maildir {relative_maildir} ({fs_err})")
            if orphans:
                self._write_rollback_failed_audit(
                    username=str(create.username),
                    domain=domain,
                    orphans=orphans,
                )
            raise

        got = self.get(create.username)
        if got is None:
            raise DBError("mailbox vanished after insert")
        return got

    def _compensate_remove_maildir(self, relative: Path) -> str | None:
        """Best-effort remove_maildir. Returns the sanitized error
        message on failure (compensation continues), None on success."""
        try:
            self._fs.remove_maildir(relative)
        except Exception as compensation_err:
            _logger.error(
                "compensation: remove_maildir(%s) failed: %s",
                relative,
                compensation_err,
            )
            return sanitize_audit_error(compensation_err)
        return None

    def _compensate_delete_mailbox_row(self, username: str) -> str | None:
        """Best-effort delete_mailbox_row with bounded retry on
        DeadlockError. Returns the sanitized error message on final
        failure (compensation continues), None on success."""
        last_err: Exception | None = None
        for attempt in range(_ROW_DELETE_RETRY_ATTEMPTS):
            try:
                self._delete_mailbox_row(username)
                return None
            except DeadlockError as deadlock_err:
                last_err = deadlock_err
                _logger.warning(
                    "compensation: delete mailbox row %s deadlocked (attempt %d/%d): %s",
                    username,
                    attempt + 1,
                    _ROW_DELETE_RETRY_ATTEMPTS,
                    deadlock_err,
                )
                time.sleep(_ROW_DELETE_RETRY_DELAY_SECONDS)
            except Exception as compensation_err:
                _logger.error(
                    "compensation: delete mailbox row %s failed: %s",
                    username,
                    compensation_err,
                )
                return sanitize_audit_error(compensation_err)
        assert last_err is not None
        _logger.error(
            "compensation: delete mailbox row %s failed after %d retries: %s",
            username,
            _ROW_DELETE_RETRY_ATTEMPTS,
            last_err,
        )
        return sanitize_audit_error(last_err)

    def _write_rollback_failed_audit(
        self, *, username: str, domain: str, orphans: list[str]
    ) -> None:
        """Side-channel audit row documenting orphan resources left
        after a failed compensation. Fresh tx since the original is
        already dead. Failure here is logged-and-swallowed (the caller
        is already raising HookError; we must not mask it)."""
        try:
            with self._engine.begin() as conn:
                self._audit.write(
                    conn,
                    action=mk_action("mailbox", "create_rollback_failed"),
                    domain=domain,
                    data=f"{username} orphans={'; '.join(orphans)}",
                )
        except Exception as side_err:
            _logger.error(
                "compensation: rollback_failed audit row for %s also failed: %s",
                username,
                side_err,
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

    def is_idp_managed(self, username: EmailStr) -> bool:
        """Return True if `username`'s row currently belongs to the
        external IdP.

        Used by CLI guards (`user passwd --claim`, `user release`) to
        decide whether a credential rotation crosses the IdP↔SQL boundary.
        Delegates entirely to the active ``IdentityProvider`` — the
        ``{NOAUTH}`` sentinel literal is private to the providers.

        Raises ``NotFoundError`` if the mailbox row does not exist."""
        with self._engine.connect() as conn:
            return self._identity.is_idp_managed(conn, str(username))

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
                    password=self._identity.bootstrap_password_value(),
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

        Two-phase maildir delete: an atomic ``os.rename`` of the maildir
        to a ``.deleting.<token>`` graveyard runs inside the DB
        transaction. The full ``rmtree`` runs *outside* the tx, after
        commit. Rationale: ``rmtree`` is non-atomic — a mid-walk OSError
        used to roll the DB tx back over a partially-wiped maildir,
        leaving an inconsistent on-disk state restored under a live row
        (silent message loss). The two-phase rename keeps the tx
        dependency atomic; a rmtree failure now leaves a
        ``.deleting.*`` artefact for ``postino check --deep`` to sweep,
        which is recoverable without data loss.

        Logs a WARNING for every alias whose `goto` references this
        username — the alias survives but now has a dead recipient. The
        admin chooses whether to clean up; spec policy is non-blocking.

        Raises ``NotFoundError`` if the mailbox row does not exist,
        ``FilesystemError`` if maildir staging fails (rmtree failure is
        logged and surfaced via the graveyard, not raised)."""
        mailbox = self._md.tables["mailbox"]
        existing = self.get(username)
        if existing is None:
            raise NotFoundError(f"mailbox {username} does not exist")
        relative = existing.maildir
        orphan_aliases = self._aliases_targeting(str(username))
        staged: Path | None = None
        with translate_db_errors(), self._engine.begin() as conn:
            self._identity.delete_identity(conn, str(username))
            quota2 = self._md.tables["quota2"]
            conn.execute(quota2.delete().where(quota2.c.username == str(username)))
            conn.execute(mailbox.delete().where(mailbox.c.username == str(username)))
            self._audit.write(
                conn,
                action=mk_action("mailbox", "delete"),
                domain=existing.domain,
                data=str(username),
            )
            if not keep_maildir:
                staged = self._fs.stage_maildir_for_delete(relative)
        # DB tx committed; purge the graveyard outside the tx. rmtree
        # failure here is non-fatal — leaves a .deleting.* artefact
        # visible to `postino check --deep`. Do not re-raise: the
        # delete is already done from the DB perspective.
        if staged is not None:
            try:
                self._fs.purge_staged_maildir(staged)
            except Exception:
                _logger.exception(
                    "post-commit purge of staged maildir %s for %s failed; "
                    ".deleting.* artefact left for check --deep to sweep",
                    staged,
                    username,
                )
        if orphan_aliases:
            _logger.warning(
                "deleted %s; %d alias(es) still target it: %s",
                username,
                len(orphan_aliases),
                ", ".join(sorted(orphan_aliases)),
            )

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
        _, _, domain = str(username).partition("@")
        with translate_db_errors(), self._engine.begin() as conn:
            self._identity.set_password(conn, str(username), password, scheme)
            self._audit.write(
                conn,
                action=mk_action("mailbox", "set_password"),
                domain=domain,
                data=str(username),
            )

    def release_identity(self, username: EmailStr) -> None:
        """Release the mailbox credential to the IdP (``{NOAUTH}`` sentinel).

        Idempotent at the DB level: a row already on the sentinel returns
        without password column changes; an audit row is still written to
        record the operator intent. Only meaningful under
        identity_backend=hybrid; LocalProvider raises ConfigError, and
        NoAuthProvider returns silently (sentinel already in place)."""
        _, _, domain = str(username).partition("@")
        with translate_db_errors(), self._engine.begin() as conn:
            self._identity.release_identity(conn, str(username))
            self._audit.write(
                conn,
                action=mk_action("mailbox", "release"),
                domain=domain,
                data=str(username),
            )

    def set_name(self, username: EmailStr, name: str) -> None:
        """Update the mailbox display name."""
        mailbox = self._md.tables["mailbox"]
        now = self._clock()
        _, _, domain = str(username).partition("@")
        with translate_db_errors(), self._engine.begin() as conn:
            result = conn.execute(
                mailbox.update()
                .where(mailbox.c.username == str(username))
                .values(name=name, modified=now)
            )
            if result.rowcount == 0:
                raise NotFoundError(f"mailbox {username} does not exist")
            self._audit.write(
                conn,
                action=mk_action("mailbox", "set_name"),
                domain=domain,
                data=str(username),
            )

    def set_status(self, username: EmailStr, status: MailboxStatus) -> None:
        """Enable / disable the mailbox."""
        mailbox = self._md.tables["mailbox"]
        now = self._clock()
        _, _, domain = str(username).partition("@")
        with translate_db_errors(), self._engine.begin() as conn:
            result = conn.execute(
                mailbox.update()
                .where(mailbox.c.username == str(username))
                .values(active=int(status), modified=now)
            )
            if result.rowcount == 0:
                raise NotFoundError(f"mailbox {username} does not exist")
            self._audit.write(
                conn,
                action=mk_action("mailbox", "set_status"),
                domain=domain,
                data=f"{username}={status.name}",
            )

    def set_quota(self, username: EmailStr, quota_bytes: int) -> None:
        """Set the per-mailbox quota cap."""
        if quota_bytes < 0:
            from postino_core.errors import ConfigError

            raise ConfigError("quota_bytes cannot be negative")
        mailbox = self._md.tables["mailbox"]
        now = self._clock()
        _, _, domain = str(username).partition("@")
        with translate_db_errors(), self._engine.begin() as conn:
            result = conn.execute(
                mailbox.update()
                .where(mailbox.c.username == str(username))
                .values(quota=quota_bytes, modified=now)
            )
            if result.rowcount == 0:
                raise NotFoundError(f"mailbox {username} does not exist")
            self._audit.write(
                conn,
                action=mk_action("mailbox", "set_quota"),
                domain=domain,
                data=f"{username}={quota_bytes}",
            )
