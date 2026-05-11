"""Audit-log writes to PostfixAdmin's `log` table.

The PA web UI shows whatever lives in the `log` table; postino emits its
own rows under the `postino.<resource>.<verb>` namespace so admins
inspecting the same UI see CLI-driven mutations alongside web-UI ones.

The mutator services accept an `AuditWriter` (Protocol) and call its
`write()` inside the same `engine.begin()` block that performs the
mutation, so the audit row commits atomically with the mutation —
no mid-flight failure can leave a row that says "we did X" while the
underlying mutation rolled back, or vice versa.

postino_core never imports `getpass`. The CLI passes
`getpass.getuser` to the writer at service-bundle construction time
(`tests/architecture/test_no_getpass_in_core.py` enforces the boundary).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import MetaData
from sqlalchemy.engine import Connection

# Reserved action prefix; spec §5.6.
ACTION_PREFIX = "postino"

# postinod surface — written by postinod, kept alongside postino.* for
# one-glance audit reading.
POSTINOD_ACTION_PREFIX = "postinod"


def mk_action(resource: str, verb: str) -> str:
    """Compose `postino.<resource>.<verb>` (spec §5.6 namespacing)."""
    return f"{ACTION_PREFIX}.{resource}.{verb}"


def mk_postinod_action(resource: str, verb: str) -> str:
    """Compose `postinod.<resource>.<verb>` (spec §5.6 namespacing)."""
    return f"{POSTINOD_ACTION_PREFIX}.{resource}.{verb}"


_DEFAULT_ACTOR = "postino"


def default_actor() -> str:
    """Fallback identity when no real actor is available.

    Returns the literal ``"postino"``. postino_core MUST NOT call
    ``getpass.getuser`` — the CLI passes ``getpass.getuser`` explicitly
    when it constructs the writer."""
    return _DEFAULT_ACTOR


class AuditWriter(Protocol):
    """Records one (or more) audit rows on the caller's Connection.

    Implementations participate in the caller's open transaction:
    the `conn` argument is the Connection inside an outer
    `engine.begin()`. The writer must NOT open its own transaction
    or call `engine.begin()`."""

    def write(
        self,
        conn: Connection,
        *,
        action: str,
        domain: str,
        data: str,
    ) -> None: ...


@dataclass(frozen=True)
class DefaultAuditWriter:
    """Vanilla one-row writer: inserts a single `postino.*` audit row.

    Used by the CLI and by tests that don't need the postinod-side
    mirror row. `actor()` is injected so the same writer works for
    interactive CLI use (`getpass.getuser`), for daemon use (resolved
    from the request context), and for tests (lambda).
    """

    metadata: MetaData
    clock: Callable[[], datetime]
    actor: Callable[[], str] = default_actor

    def write(
        self,
        conn: Connection,
        *,
        action: str,
        domain: str,
        data: str,
    ) -> None:
        log = self.metadata.tables["log"]
        conn.execute(
            log.insert().values(
                timestamp=self.clock(),
                username=self.actor(),
                domain=domain,
                action=action,
                data=data,
            )
        )


def write_audit(
    conn: Connection,
    md: MetaData,
    *,
    clock: Callable[[], datetime],
    action: str,
    domain: str,
    data: str,
    actor: Callable[[], str] = default_actor,
) -> None:
    """Insert one row into PA's `log` table.

    Free-function form retained for call sites that already hold a
    Connection but do not have access to an AuditWriter (notably
    `postinod.audit.write_postinod_audit`, which is used for
    side-channel audit rows such as `postinod.zitadel.replay` that have
    no matching `postino.*` mutation).

    Caller is responsible for namespacing `action` (use `mk_action()`).
    """
    log = md.tables["log"]
    conn.execute(
        log.insert().values(
            timestamp=clock(),
            username=actor(),
            domain=domain,
            action=action,
            data=data,
        )
    )
