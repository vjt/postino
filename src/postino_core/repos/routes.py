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

from pydantic import BaseModel, ConfigDict


_MLMMJ_SUFFIXES: tuple[tuple[str, str, int], ...] = (
    # (suffix-fragment, transport, priority)
    # priority 10 = specific-suffix; priority 50 = catchall last
    ("-bounces", "mlmmj-bounce:", 10),
    ("-confirm-sub-.+", "mlmmj-sub:", 10),
    ("-confirm-unsub-.+", "mlmmj-unsub:", 10),
    ("-help", "mlmmj-help:", 10),
)


def _mlmmj_patterns(list_address: str) -> list[tuple[str, str, int]]:  # pyright: ignore[reportUnusedFunction]  # WHY: called from tests + future RoutesRepository; not yet wired into repo CRUD
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
