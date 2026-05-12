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
from sqlalchemy.exc import IntegrityError

from postino_core.audit import AuditWriter, DefaultAuditWriter, mk_action
from postino_core.db import translate_db_errors
from postino_core.enums import MailboxStatus
from postino_core.errors import AlreadyExistsError, NotFoundError, RuleViolationError
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

    def add(self, alias_domain: str, *, target: str) -> AliasDomain:
        """Create an alias_domain row mapping ``alias_domain`` -> ``target``.

        Six validation rules enforced inside the same transaction as the
        INSERT, so concurrent writes cannot race past them:

        1. no self-alias (source != target);
        2. source domain must exist in ``domain``;
        3. target domain must exist in ``domain``;
        4. source must not already be the target of another row (no chain);
        5. target must not already be the source of another row (no chain);
        6. row must not already exist.

        Raises: RuleViolationError on 1/4/5; NotFoundError on 2/3;
                AlreadyExistsError on 6; DBError on driver-level errors.
        """
        self._validate_pair(alias_domain, target)
        now = self._clock()
        t = self._md.tables["alias_domain"]
        domains = self._md.tables["domain"]
        with translate_db_errors(), self._engine.begin() as conn:
            # Rules 2, 3: both endpoints must exist as domains.
            for d in (alias_domain, target):
                hit = conn.execute(select(domains.c.domain).where(domains.c.domain == d)).first()
                if hit is None:
                    raise NotFoundError(f"domain {d} does not exist")
            # Rules 4, 5: no chain — alias_domain not already a target,
            # target not already a source. Single round-trip via OR.
            # with_for_update() lock-reads under InnoDB REPEATABLE READ so
            # two concurrent writers can't both pass validation.
            chain = conn.execute(
                select(t.c.alias_domain)
                .where((t.c.target_domain == alias_domain) | (t.c.alias_domain == target))
                .with_for_update()
            ).first()
            if chain is not None:
                raise RuleViolationError(
                    f"adding {alias_domain} -> {target} would chain "
                    "with an existing alias_domain row"
                )
            # Rule 6: row uniqueness is enforced by the alias_domain PK;
            # translate the IntegrityError instead of a redundant pre-flight
            # SELECT (which would lose the race under contention).
            try:
                conn.execute(
                    t.insert().values(
                        alias_domain=alias_domain,
                        target_domain=target,
                        created=now,
                        modified=now,
                        active=int(MailboxStatus.ACTIVE),
                    )
                )
            except IntegrityError as e:
                raise AlreadyExistsError(f"alias_domain {alias_domain} already exists") from e
            self._audit.write(
                conn,
                action=mk_action("alias_domain", "add"),
                domain=alias_domain,
                data=f"{alias_domain}->{target}",
            )
        return self.get(alias_domain)

    def delete(self, alias_domain: str) -> None:
        t = self._md.tables["alias_domain"]
        with translate_db_errors(), self._engine.begin() as conn:
            result = conn.execute(t.delete().where(t.c.alias_domain == alias_domain))
            if result.rowcount == 0:
                raise NotFoundError(f"alias_domain {alias_domain} does not exist")
            self._audit.write(
                conn,
                action=mk_action("alias_domain", "delete"),
                domain=alias_domain,
                data=alias_domain,
            )

    def set_status(self, alias_domain: str, status: MailboxStatus) -> None:
        t = self._md.tables["alias_domain"]
        now = self._clock()
        with translate_db_errors(), self._engine.begin() as conn:
            result = conn.execute(
                t.update()
                .where(t.c.alias_domain == alias_domain)
                .values(active=int(status), modified=now)
            )
            if result.rowcount == 0:
                raise NotFoundError(f"alias_domain {alias_domain} does not exist")
            self._audit.write(
                conn,
                action=mk_action("alias_domain", "set_status"),
                domain=alias_domain,
                data=f"{alias_domain}={status.name}",
            )

    def retarget(self, alias_domain: str, *, target: str) -> AliasDomain:
        self._validate_pair(alias_domain, target)
        now = self._clock()
        t = self._md.tables["alias_domain"]
        domains = self._md.tables["domain"]
        with translate_db_errors(), self._engine.begin() as conn:
            # Row must exist.
            existing = conn.execute(
                select(t.c.alias_domain).where(t.c.alias_domain == alias_domain).with_for_update()
            ).first()
            if existing is None:
                raise NotFoundError(f"alias_domain {alias_domain} does not exist")
            # Target domain must exist.
            tgt_hit = conn.execute(
                select(domains.c.domain).where(domains.c.domain == target)
            ).first()
            if tgt_hit is None:
                raise NotFoundError(f"domain {target} does not exist")
            # Rule 5: target must not itself be an alias_domain source.
            chain = conn.execute(
                select(t.c.alias_domain).where(t.c.alias_domain == target).with_for_update()
            ).first()
            if chain is not None:
                raise RuleViolationError(
                    f"retargeting {alias_domain} to {target} would chain "
                    "with an existing alias_domain row"
                )
            conn.execute(
                t.update()
                .where(t.c.alias_domain == alias_domain)
                .values(target_domain=target, modified=now)
            )
            self._audit.write(
                conn,
                action=mk_action("alias_domain", "retarget"),
                domain=alias_domain,
                data=f"{alias_domain}->{target}",
            )
        return self.get(alias_domain)

    @staticmethod
    def _validate_pair(alias_domain: str, target: str) -> None:
        # Rule 1: no self-alias.
        if alias_domain == target:
            raise RuleViolationError(
                f"alias_domain {alias_domain} cannot self-alias (source == target)"
            )

    def _row_to_model(self, row: RowMapping) -> AliasDomain:
        return AliasDomain(
            alias_domain=str(row["alias_domain"]),
            target_domain=str(row["target_domain"]),
            status=MailboxStatus(int(row["active"])),  # type: ignore[arg-type]  # WHY: SQLAlchemy RowMapping returns Any; int() narrows safely.
            created=row["created"],  # type: ignore[arg-type]  # WHY: SQLAlchemy RowMapping returns Any; the column is DATETIME NOT NULL.
            modified=row["modified"],  # type: ignore[arg-type]  # WHY: SQLAlchemy RowMapping returns Any; the column is DATETIME NOT NULL.
        )
