"""DomainService — CRUD on the PA domain table.

`delete` cascades dependents (mailboxes, aliases, alias_domain, domain
admins) only when `force=True`; otherwise it refuses a non-empty domain.
After the DB transaction commits, the per-domain maildir tree is removed
on disk; that step is best-effort and does not strand the DB cleanup if
it fails."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy import MetaData, func, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.row import RowMapping
from sqlalchemy.exc import IntegrityError

from postino_core.audit import mk_action, write_audit
from postino_core.db import translate_db_errors
from postino_core.enums import DomainTransport, MailboxStatus
from postino_core.errors import AlreadyExistsError, CapacityError, DBError, NotFoundError
from postino_core.fs import FilesystemAdapter
from postino_core.models import Domain

_logger = logging.getLogger(__name__)


class DomainService:
    def __init__(
        self,
        *,
        engine: Engine,
        metadata: MetaData,
        clock: Callable[[], datetime],
        fs: FilesystemAdapter,
        lmtp_destination: str,
    ) -> None:
        self._engine = engine
        self._md = metadata
        self._clock = clock
        self._fs = fs
        self._lmtp_destination = lmtp_destination

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
            write_audit(
                conn,
                self._md,
                clock=self._clock,
                action=mk_action("domain", "create"),
                domain=domain,
                data=domain,
            )
        got = self.get(domain)
        if got is None:
            raise DBError("domain vanished after insert")
        return got

    def get(self, domain: str) -> Domain | None:
        d = self._md.tables["domain"]
        with self._engine.connect() as conn:
            row = conn.execute(select(d).where(d.c.domain == domain)).fetchone()
        if row is None:
            return None
        return self._row_to_model(row._mapping)  # type: ignore[arg-type]

    def delete(self, domain: str, *, force: bool = False) -> None:
        """Delete a domain row.

        With ``force=False`` (default), refuses to delete a domain that
        still has mailboxes, aliases, alias_domain mappings, or domain
        admins, raising ``CapacityError`` with a count breakdown.

        With ``force=True``, cascade-deletes dependents in dependency
        order (alias_domain → alias → quota2 → mailbox → domain_admins
        → domain) inside one transaction, then removes the per-domain
        maildir tree on disk best-effort.
        """
        d = self._md.tables["domain"]
        mailbox = self._md.tables["mailbox"]
        alias = self._md.tables["alias"]
        alias_domain = self._md.tables["alias_domain"]
        domain_admins = self._md.tables["domain_admins"]
        quota2 = self._md.tables["quota2"]

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
            write_audit(
                conn,
                self._md,
                clock=self._clock,
                action=mk_action("domain", "delete"),
                domain=domain,
                data=f"{domain} force={force}",
            )

        # Post-commit: filesystem cleanup. Best-effort; a stranded
        # maildir tree is recoverable via `postino check`, while a
        # mid-failure here must not raise on top of a successful DB
        # cascade.
        try:
            self._fs.remove_maildir(Path(domain))
        except Exception as compensation_err:
            _logger.error(
                "post-commit: remove_maildir(%s) failed: %s",
                domain,
                compensation_err,
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
            rows = conn.execute(select(d).order_by(d.c.domain)).fetchall()
        return [self._row_to_model(r._mapping) for r in rows]  # type: ignore[arg-type]

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
