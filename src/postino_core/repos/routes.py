"""RoutesRepository — CRUD on the postino_core v0.10 `routes` table.

`routes` carries postfix `transport_maps` data in SQL. Each mailing list
contributes 5 regex-pattern rows that map per-suffix recipient patterns
to mlmmj binary transports. Postfix consults via a `transport_maps =
mysql:...` source ordered ahead of PA's existing domain-transport
source.

Schema lives in tests/fixtures/postfixadmin.sql (test) and is reflected
at runtime — postino never declares it via SQLAlchemy `Table(...)`."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Route(BaseModel):
    """One row of the routes table."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    pattern: str
    transport: str
    domain: str
    list_address: str | None
    priority: int
    active: bool
