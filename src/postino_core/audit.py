"""Audit-log writes to PostfixAdmin's `log` table.

The PA web UI shows whatever lives in the `log` table; postino emits
its own rows under the `postino.<resource>.<verb>` namespace so admins
inspecting the same UI see CLI-driven mutations alongside web-UI ones.

The helper participates in the caller's open transaction (the `conn`
parameter is the Connection inside an outer `engine.begin()`) so the
audit row commits atomically with the mutation it describes — no
mid-flight failure can leave a row that says "we did X" while the
underlying mutation rolled back, or vice versa.
"""

from __future__ import annotations

import getpass
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import MetaData
from sqlalchemy.engine import Connection

# Reserved action prefix; spec §5.6.
ACTION_PREFIX = "postino"


def actor() -> str:
    """Identity recorded in `log.username` — the OS user running postino.

    Falls back to ``"postino"`` if no controlling tty / user lookup is
    available (e.g. a daemonised invocation with a stripped env)."""
    try:
        return getpass.getuser()
    except OSError:
        return "postino"


def write_audit(
    conn: Connection,
    md: MetaData,
    *,
    clock: Callable[[], datetime],
    action: str,
    domain: str,
    data: str,
) -> None:
    """Insert one row into PA's `log` table.

    Caller is responsible for namespacing `action` (use `mk_action`).
    Caller-supplied `data` is a free-form string identifying the
    mutation target (e.g. the username, alias address, etc.)."""
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


def mk_action(resource: str, verb: str) -> str:
    """Compose `postino.<resource>.<verb>` (spec §5.6 namespacing)."""
    return f"{ACTION_PREFIX}.{resource}.{verb}"
