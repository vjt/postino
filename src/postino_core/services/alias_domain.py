"""AliasDomainService — CRUD over the PostfixAdmin alias_domain table.

Strict validation guards against postfix delivery loops:
self-alias, source-already-target, target-already-source. The
filesystem layer is untouched; alias_domain has no per-domain
maildir or transport coupling."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import MetaData, select
from sqlalchemy.engine import Engine
from sqlalchemy.engine.row import RowMapping

from postino_core.audit import AuditWriter, DefaultAuditWriter
from postino_core.db import translate_db_errors
from postino_core.enums import MailboxStatus
from postino_core.errors import NotFoundError
from postino_core.models import AliasDomain


class AliasDomainService:
    def __init__(
        self,
        *,
        engine: Engine,
        metadata: MetaData,
        clock: Callable[[], datetime],
        audit_writer: AuditWriter | None = None,
    ) -> None:
        self._engine = engine
        self._md = metadata
        self._clock = clock
        self._audit: AuditWriter = audit_writer or DefaultAuditWriter(
            metadata=metadata, clock=clock
        )

    def list(
        self,
        *,
        target: str | None = None,
        include_disabled: bool = False,
    ) -> list[AliasDomain]:
        """List alias_domain rows.

        Args:
            target: when set, return only rows aliasing *to* this domain.
            include_disabled: when False (default), filter to active=1 only.

        Returns: rows ordered by ``alias_domain`` ascending.
        """
        t = self._md.tables["alias_domain"]
        stmt = select(t)
        if target is not None:
            stmt = stmt.where(t.c.target_domain == target)
        if not include_disabled:
            stmt = stmt.where(t.c.active == 1)
        stmt = stmt.order_by(t.c.alias_domain)
        with translate_db_errors(), self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [self._row_to_model(r) for r in rows]

    def get(self, alias_domain: str) -> AliasDomain:
        """Return the alias_domain row.

        Raises: NotFoundError if the row does not exist.
        """
        t = self._md.tables["alias_domain"]
        with translate_db_errors(), self._engine.connect() as conn:
            row = (
                conn.execute(select(t).where(t.c.alias_domain == alias_domain))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise NotFoundError(f"alias_domain {alias_domain} does not exist")
        return self._row_to_model(row)

    def _row_to_model(self, row: RowMapping) -> AliasDomain:
        return AliasDomain(
            alias_domain=str(row["alias_domain"]),
            target_domain=str(row["target_domain"]),
            status=MailboxStatus(int(row["active"])),  # type: ignore[arg-type]  # WHY: SQLAlchemy RowMapping returns Any; int() narrows safely.
            created=row["created"],  # type: ignore[arg-type]  # WHY: SQLAlchemy RowMapping returns Any; the column is DATETIME NOT NULL.
            modified=row["modified"],  # type: ignore[arg-type]  # WHY: SQLAlchemy RowMapping returns Any; the column is DATETIME NOT NULL.
        )
