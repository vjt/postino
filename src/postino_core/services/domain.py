"""DomainService — CRUD on the PA domain table."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import MetaData, select
from sqlalchemy.engine import Engine
from sqlalchemy.engine.row import RowMapping
from sqlalchemy.exc import IntegrityError

from postino_core.enums import DomainTransport, MailboxStatus
from postino_core.errors import AlreadyExistsError, DBError, NotFoundError
from postino_core.models import Domain


class DomainService:
    def __init__(
        self,
        *,
        engine: Engine,
        metadata: MetaData,
        clock: Callable[[], datetime],
    ) -> None:
        self._engine = engine
        self._md = metadata
        self._clock = clock

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
        with self._engine.begin() as conn:
            try:
                conn.execute(d.insert().values(
                    domain=domain,
                    description=description,
                    aliases=max_aliases,
                    mailboxes=max_mailboxes,
                    maxquota=max_quota_bytes,
                    quota=default_quota_bytes,
                    transport=transport.value,
                    backupmx=int(backupmx),
                    active=int(MailboxStatus.ACTIVE),
                    created=now,
                    modified=now,
                ))
            except IntegrityError as e:
                raise AlreadyExistsError(f"domain {domain!r} already exists") from e
        got = self.get(domain)
        if got is None:
            raise DBError("domain vanished after insert")
        return got

    def get(self, domain: str) -> Domain | None:
        d = self._md.tables["domain"]
        with self._engine.connect() as conn:
            row = conn.execute(
                select(d).where(d.c.domain == domain)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_model(row._mapping)  # type: ignore[arg-type]

    def delete(self, domain: str) -> None:
        d = self._md.tables["domain"]
        with self._engine.begin() as conn:
            result = conn.execute(d.delete().where(d.c.domain == domain))
            if result.rowcount == 0:
                raise NotFoundError(f"domain {domain!r} does not exist")

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
            transport=DomainTransport(m["transport"]),  # type: ignore[arg-type]
            backupmx=bool(int(m["backupmx"])),  # type: ignore[arg-type]
            status=MailboxStatus(int(m["active"])),  # type: ignore[arg-type]
            created=m["created"],  # type: ignore[arg-type]
            modified=m["modified"],  # type: ignore[arg-type]
        )
