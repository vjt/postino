"""DomainService — CRUD on the PA domain table.

`delete` cascades dependents (mailboxes, aliases, alias_domain, domain
admins) only when `force=True`; otherwise it refuses a non-empty domain.
The per-domain maildir tree is removed via a two-phase delete: an atomic
rename to ``.deleting.<token>`` runs inside the DB transaction; the full
``rmtree`` runs after commit. Rmtree failure leaves a graveyard for
``postino check --deep`` to sweep — never a partial wipe restored over
by a DB rollback."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy import MetaData, func, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.row import RowMapping
from sqlalchemy.exc import IntegrityError

from postino_core.audit import AuditWriter, DefaultAuditWriter, mk_action
from postino_core.db import translate_db_errors
from postino_core.enums import DomainTransport, MailboxStatus
from postino_core.errors import (
    AlreadyExistsError,
    CapacityError,
    ConfigError,
    DBError,
    NotFoundError,
)
from postino_core.fs import FilesystemAdapter
from postino_core.models import Domain

_logger = logging.getLogger(__name__)

# PostfixAdmin's `domain` table reserves the literal `'ALL'` row as a permission
# system marker (super-admin scope in `domain_admins`). It has no routable mail
# semantics — empty `transport`, zero capacities, fixed `created`/`modified`.
# All read paths exclude it; write paths reject the name.
_PA_PERMISSION_PSEUDO_DOMAIN = "ALL"


class DomainService:
    def __init__(
        self,
        *,
        engine: Engine,
        metadata: MetaData,
        clock: Callable[[], datetime],
        fs: FilesystemAdapter,
        lmtp_destination: str,
        audit_writer: AuditWriter | None = None,
    ) -> None:
        self._engine = engine
        self._md = metadata
        self._clock = clock
        self._fs = fs
        self._lmtp_destination = lmtp_destination
        self._audit: AuditWriter = audit_writer or DefaultAuditWriter(
            metadata=metadata, clock=clock
        )

    def _transport_to_db(self, transport: DomainTransport) -> str:
        """Render an enum value into postfix's transport_maps cell.

        LMTP needs a `lmtp:<nexthop>` pair; other protocols stand alone."""
        if transport is DomainTransport.LMTP:
            return f"lmtp:{self._lmtp_destination}"
        return transport.value

    @staticmethod
    def _transport_from_db(raw: str) -> DomainTransport:
        """Parse a postfix transport_maps cell back to the enum.

        Any value starting with ``lmtp:`` collapses to ``LMTP`` regardless
        of the embedded nexthop — postino owns the protocol choice; the
        nexthop is stack-config and read from PostinoSettings."""
        if raw.startswith("lmtp:") or raw == "lmtp":
            return DomainTransport.LMTP
        return DomainTransport(raw)

    def add(
        self,
        *,
        domain: str,
        description: str,
        max_aliases: int,
        max_mailboxes: int,
        max_quota_bytes: int,
        default_quota_bytes: int,
        transport: DomainTransport,
        backupmx: bool,
    ) -> Domain:
        if domain == _PA_PERMISSION_PSEUDO_DOMAIN:
            raise ConfigError(
                f"'{_PA_PERMISSION_PSEUDO_DOMAIN}' is reserved by PostfixAdmin "
                "and cannot be used as a domain name"
            )
        d = self._md.tables["domain"]
        now = self._clock()
        with translate_db_errors(), self._engine.begin() as conn:
            try:
                conn.execute(
                    d.insert().values(
                        domain=domain,
                        description=description,
                        aliases=max_aliases,
                        mailboxes=max_mailboxes,
                        maxquota=max_quota_bytes,
                        quota=default_quota_bytes,
                        transport=self._transport_to_db(transport),
                        backupmx=int(backupmx),
                        active=int(MailboxStatus.ACTIVE),
                        created=now,
                        modified=now,
                    )
                )
            except IntegrityError as e:
                raise AlreadyExistsError(f"domain {domain!r} already exists") from e
            self._audit.write(
                conn,
                action=mk_action("domain", "create"),
                domain=domain,
                data=domain,
            )
        got = self.get(domain)
        if got is None:
            raise DBError("domain vanished after insert")
        return got

    def get(self, domain: str) -> Domain | None:
        if domain == _PA_PERMISSION_PSEUDO_DOMAIN:
            return None
        d = self._md.tables["domain"]
        with self._engine.connect() as conn:
            row = conn.execute(select(d).where(d.c.domain == domain)).fetchone()
        if row is None:
            return None
        return self._row_to_model(row._mapping)  # type: ignore[arg-type]  # WHY: SQLAlchemy RowMapping is typed Any; we access known columns

    def delete(self, domain: str, *, force: bool = False, keep_maildir: bool = False) -> None:
        """Delete a domain row.

        With ``force=False`` (default), refuses to delete a domain that
        still has mailboxes, aliases, alias_domain mappings, or domain
        admins, raising ``CapacityError`` with a count breakdown.

        With ``force=True``, cascade-deletes dependents in dependency
        order (alias_domain → alias → quota2 → mailbox → domain_admins
        → domain) AND stages the per-domain maildir tree for deletion
        via an atomic ``os.rename`` to ``.deleting.<token>`` — inside
        the same transaction. The full ``rmtree`` runs after commit.
        Rename failure aborts the cascade so an operator sees a single
        error; rmtree failure post-commit leaves a ``.deleting.*``
        graveyard for ``postino check --deep`` to sweep, never a
        partially-wiped tree restored over by a DB rollback.

        ``keep_maildir`` skips the FS staging even on ``force=True`` —
        useful when the caller plans to archive the maildir tree
        before final disposal. Defaults to False; CLI exposes it via
        ``postino domain del --keep-maildir``.
        """
        if domain == _PA_PERMISSION_PSEUDO_DOMAIN:
            raise NotFoundError(
                f"domain '{_PA_PERMISSION_PSEUDO_DOMAIN}' does not exist "
                "(it is PostfixAdmin's internal permission marker)"
            )
        d = self._md.tables["domain"]
        mailbox = self._md.tables["mailbox"]
        alias = self._md.tables["alias"]
        alias_domain = self._md.tables["alias_domain"]
        domain_admins = self._md.tables["domain_admins"]
        quota2 = self._md.tables["quota2"]

        staged: Path | None = None
        with translate_db_errors(), self._engine.begin() as conn:
            row = conn.execute(
                select(d.c.domain).where(d.c.domain == domain).with_for_update()
            ).fetchone()
            if row is None:
                raise NotFoundError(f"domain {domain!r} does not exist")

            counts = self._count_dependents(conn, domain)
            if not force and any(counts.values()):
                raise CapacityError(
                    f"domain {domain!r} not empty: "
                    f"{counts['mailbox']} mailboxes, {counts['alias']} aliases, "
                    f"{counts['alias_domain']} alias_domain mappings, "
                    f"{counts['domain_admins']} admins"
                )

            if force:
                # Order matters: alias_domain (touches both sides) →
                # alias → quota2 (FK on mailbox.username) → mailbox →
                # domain_admins → domain.
                conn.execute(
                    alias_domain.delete().where(
                        (alias_domain.c.alias_domain == domain)
                        | (alias_domain.c.target_domain == domain)
                    )
                )
                conn.execute(alias.delete().where(alias.c.domain == domain))
                conn.execute(
                    quota2.delete().where(
                        quota2.c.username.in_(
                            select(mailbox.c.username).where(mailbox.c.domain == domain)
                        )
                    )
                )
                conn.execute(mailbox.delete().where(mailbox.c.domain == domain))
                conn.execute(domain_admins.delete().where(domain_admins.c.domain == domain))

            conn.execute(d.delete().where(d.c.domain == domain))
            self._audit.write(
                conn,
                action=mk_action("domain", "delete"),
                domain=domain,
                data=f"{domain} force={force} keep_maildir={keep_maildir}",
            )
            # Stage the per-domain maildir for delete INSIDE the tx —
            # only the atomic rename rides the DB tx. If the rename
            # fails (cross-FS, EACCES), the cascade rolls back and the
            # tree is untouched. rmtree runs post-commit; failure
            # leaves a .deleting.* artefact rather than risking a
            # partial wipe under a restored DB row.
            if not keep_maildir:
                staged = self._fs.stage_maildir_for_delete(Path(domain))

        if staged is not None:
            try:
                self._fs.purge_staged_maildir(staged)
            except Exception:
                _logger.exception(
                    "post-commit purge of staged per-domain maildir %s for %s failed; "
                    ".deleting.* artefact left for check --deep to sweep",
                    staged,
                    domain,
                )

    def _count_dependents(self, conn: Connection, domain: str) -> dict[str, int]:
        mailbox = self._md.tables["mailbox"]
        alias = self._md.tables["alias"]
        alias_domain = self._md.tables["alias_domain"]
        domain_admins = self._md.tables["domain_admins"]
        return {
            "mailbox": int(
                conn.execute(
                    select(func.count()).select_from(mailbox).where(mailbox.c.domain == domain)
                ).scalar_one()
            ),
            "alias": int(
                conn.execute(
                    select(func.count()).select_from(alias).where(alias.c.domain == domain)
                ).scalar_one()
            ),
            "alias_domain": int(
                conn.execute(
                    select(func.count())
                    .select_from(alias_domain)
                    .where(
                        (alias_domain.c.alias_domain == domain)
                        | (alias_domain.c.target_domain == domain)
                    )
                ).scalar_one()
            ),
            "domain_admins": int(
                conn.execute(
                    select(func.count())
                    .select_from(domain_admins)
                    .where(domain_admins.c.domain == domain)
                ).scalar_one()
            ),
        }

    def list(self) -> list[Domain]:
        d = self._md.tables["domain"]
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(d).where(d.c.domain != _PA_PERMISSION_PSEUDO_DOMAIN).order_by(d.c.domain)
            ).fetchall()
        return [self._row_to_model(r._mapping) for r in rows]  # type: ignore[arg-type]  # WHY: SQLAlchemy RowMapping is typed Any; we access known columns

    def set_status(self, name: str, status: MailboxStatus) -> None:
        """Enable / disable a domain. Mirrors MailboxService.set_status."""
        domain = self._md.tables["domain"]
        now = self._clock()
        with translate_db_errors(), self._engine.begin() as conn:
            result = conn.execute(
                domain.update()
                .where(domain.c.domain == name)
                .values(active=int(status), modified=now)
            )
            if result.rowcount == 0:
                raise NotFoundError(f"domain {name} does not exist")
            self._audit.write(
                conn,
                action=mk_action("domain", "set_status"),
                domain=name,
                data=f"{name}={status.name}",
            )

    def _row_to_model(self, m: RowMapping) -> Domain:
        return Domain(
            domain=str(m["domain"]),
            description=str(m["description"]),
            max_aliases=int(m["aliases"]),  # type: ignore[arg-type]
            max_mailboxes=int(m["mailboxes"]),  # type: ignore[arg-type]
            max_quota_bytes=int(m["maxquota"]),  # type: ignore[arg-type]
            default_quota_bytes=int(m["quota"]),  # type: ignore[arg-type]
            transport=self._transport_from_db(str(m["transport"])),
            backupmx=bool(int(m["backupmx"])),  # type: ignore[arg-type]
            status=MailboxStatus(int(m["active"])),  # type: ignore[arg-type]
            created=m["created"],  # type: ignore[arg-type]
            modified=m["modified"],  # type: ignore[arg-type]
        )
