"""AliasService — CRUD on the PA alias table."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from pydantic import EmailStr
from sqlalchemy import MetaData, func, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.row import RowMapping
from sqlalchemy.exc import IntegrityError

from postino_core.db import translate_db_errors
from postino_core.enums import MailboxStatus
from postino_core.errors import AlreadyExistsError, CapacityError, DBError, NotFoundError
from postino_core.models import Alias


class AliasService:
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

    def add(self, *, address: EmailStr, goto: str) -> Alias:
        """Create a new alias.

        Returns: the parsed Alias row.
        Raises: NotFoundError if the domain is unknown.
                CapacityError if domain.aliases cap would be exceeded.
                AlreadyExistsError on uniqueness conflict.
                DBError.
        """
        alias = self._md.tables["alias"]
        _, _, domain = str(address).partition("@")
        now = self._clock()
        with translate_db_errors(), self._engine.begin() as conn:
            self._assert_domain_capacity(conn, domain)
            try:
                conn.execute(
                    alias.insert().values(
                        address=str(address),
                        goto=goto,
                        domain=domain,
                        created=now,
                        modified=now,
                        active=int(MailboxStatus.ACTIVE),
                    )
                )
            except IntegrityError as e:
                raise AlreadyExistsError(f"alias {address} already exists") from e
        got = self.get(address)
        if got is None:
            raise DBError("alias vanished after insert")
        return got

    def _assert_domain_capacity(self, conn: Connection, domain: str) -> None:
        d = self._md.tables["domain"]
        a = self._md.tables["alias"]
        row = conn.execute(
            select(d.c.aliases).where(d.c.domain == domain).with_for_update()
        ).fetchone()
        if row is None:
            raise NotFoundError(f"domain {domain!r} does not exist")
        cap = int(row[0])
        if cap > 0:
            count = conn.execute(
                select(func.count()).select_from(a).where(a.c.domain == domain)
            ).scalar_one()
            if count >= cap:
                raise CapacityError(f"domain {domain!r} reached max_aliases={cap}")

    def get(self, address: EmailStr) -> Alias | None:
        """Return the alias or None if absent."""
        alias = self._md.tables["alias"]
        with self._engine.connect() as conn:
            row = conn.execute(select(alias).where(alias.c.address == str(address))).fetchone()
        if row is None:
            return None
        return self._row_to_model(row._mapping)  # type: ignore[arg-type]

    def delete(self, address: EmailStr) -> None:
        """Delete the alias row.

        Raises: NotFoundError if the alias does not exist.
        """
        alias = self._md.tables["alias"]
        with translate_db_errors(), self._engine.begin() as conn:
            result = conn.execute(alias.delete().where(alias.c.address == str(address)))
            if result.rowcount == 0:
                raise NotFoundError(f"alias {address} does not exist")

    def list(self, *, domain: str | None = None) -> list[Alias]:
        """List aliases, optionally scoped to a domain.

        Returns aliases ordered by address ascending.
        """
        alias = self._md.tables["alias"]
        stmt = select(alias).order_by(alias.c.address)
        if domain is not None:
            stmt = stmt.where(alias.c.domain == domain)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [self._row_to_model(r._mapping) for r in rows]  # type: ignore[arg-type]

    def _row_to_model(self, m: RowMapping) -> Alias:
        return Alias(
            address=str(m["address"]),  # type: ignore[arg-type]
            goto=str(m["goto"]),  # type: ignore[arg-type]
            domain=str(m["domain"]),  # type: ignore[arg-type]
            status=MailboxStatus(int(m["active"])),  # type: ignore[arg-type]
            created=m["created"],  # type: ignore[arg-type]
            modified=m["modified"],  # type: ignore[arg-type]
        )
