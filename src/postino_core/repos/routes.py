"""RoutesRepository — CRUD on the postino_core v0.10 `routes` table.

`routes` carries postfix `transport_maps` data in SQL. Each mailing list
contributes 5 regex-pattern rows that map per-suffix recipient patterns
to mlmmj binary transports. Postfix consults via a `transport_maps =
mysql:...` source ordered ahead of PA's existing domain-transport
source.

Schema lives in tests/fixtures/postfixadmin.sql (test) and is reflected
at runtime — postino never declares it via SQLAlchemy `Table(...)`."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import MetaData
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.row import RowMapping

_MLMMJ_SUFFIXES: tuple[tuple[str, str, int], ...] = (
    # (suffix-fragment, transport, priority)
    # priority 10 = specific-suffix; priority 50 = catchall last
    ("-bounces", "mlmmj-bounce:", 10),
    ("-confirm-sub-.+", "mlmmj-sub:", 10),
    ("-confirm-unsub-.+", "mlmmj-unsub:", 10),
    ("-help", "mlmmj-help:", 10),
)


def _mlmmj_patterns(list_address: str) -> list[tuple[str, str, int]]:
    """Return the 5 (pattern, transport, priority) tuples for one list.

    Patterns are localpart-anchored — each list owns its own pattern set;
    no domain-wide `^.+-bounces@` regex that would collide across
    multiple lists on the same domain.

    Raises ValueError for inputs that don't look like a valid email.
    """
    if "@" not in list_address:
        raise ValueError(f"list_address {list_address!r}: missing '@'")
    localpart, _, domain = list_address.rpartition("@")
    if not localpart or not domain:
        raise ValueError(f"list_address {list_address!r}: empty local-part or domain")
    lp_re = re.escape(localpart)
    dom_re = re.escape(domain)
    rows = [
        (rf"^{lp_re}{suffix}@{dom_re}$", transport, priority)
        for suffix, transport, priority in _MLMMJ_SUFFIXES
    ]
    rows.append((rf"^{lp_re}(\+.+)?@{dom_re}$", "mlmmj-receive:", 50))
    return rows


class Route(BaseModel):
    """One row of the routes table."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    pattern: str
    transport: str
    domain: str
    list_address: str | None
    priority: int
    active: bool


class RoutesRepository:
    """CRUD on the `routes` table.

    The repository is intentionally thin: SQLAlchemy reflection-based,
    no per-row Pydantic validation on writes (the rows are deterministic
    from list_address). Read paths return `Route` models for typed
    consumption."""

    def __init__(self, *, engine: Engine, metadata: MetaData) -> None:
        self._engine = engine
        self._md = metadata

    def insert_mlmmj_list(self, conn: Connection, list_address: EmailStr) -> None:
        """Write the 5 per-list routes rows for an mlmmj mailing list.

        Caller owns the transaction (typical use: inside
        `MailingListService.add`'s single tx so routes + alias + spool
        commit atomically). The PRIMARY KEY on `pattern` enforces
        uniqueness; concurrent inserts for the same list raise
        `IntegrityError` from the DBAPI."""
        addr = str(list_address)
        _, _, domain = addr.rpartition("@")
        routes = self._md.tables["routes"]
        rows = [
            {
                "pattern": pattern,
                "transport": transport,
                "domain": domain,
                "list_address": addr,
                "priority": priority,
                "active": 1,
            }
            for pattern, transport, priority in _mlmmj_patterns(addr)
        ]
        conn.execute(routes.insert(), rows)

    def delete_by_list_address(self, conn: Connection, list_address: EmailStr) -> int:
        """Remove all routes rows referencing the given list address.

        Returns: number of rows deleted (5 for a properly-provisioned
        list, 0 if the list never had routes)."""
        routes = self._md.tables["routes"]
        result = conn.execute(routes.delete().where(routes.c.list_address == str(list_address)))
        return int(result.rowcount or 0)

    def list_by_domain(self, conn: Connection, domain: str) -> list[Route]:
        """All routes rows for a given domain."""
        routes = self._md.tables["routes"]
        rows = conn.execute(
            routes.select().where(routes.c.domain == domain).order_by(routes.c.priority)
        ).fetchall()
        return [self._row_to_route(r._mapping) for r in rows]  # pyright: ignore[reportPrivateUsage]  # WHY: SQLAlchemy Row._mapping is public API despite the underscore prefix.

    def list_by_list_address(self, conn: Connection, list_address: EmailStr) -> list[Route]:
        """All routes rows referencing a specific list address."""
        routes = self._md.tables["routes"]
        rows = conn.execute(
            routes.select()
            .where(routes.c.list_address == str(list_address))
            .order_by(routes.c.priority)
        ).fetchall()
        return [self._row_to_route(r._mapping) for r in rows]  # pyright: ignore[reportPrivateUsage]  # WHY: SQLAlchemy Row._mapping is public API despite the underscore prefix.

    @staticmethod
    def _row_to_route(m: RowMapping) -> Route:
        return Route(
            pattern=str(m["pattern"]),
            transport=str(m["transport"]),
            domain=str(m["domain"]),
            list_address=(str(m["list_address"]) if m["list_address"] is not None else None),
            priority=int(m["priority"]),
            active=bool(m["active"]),
        )
