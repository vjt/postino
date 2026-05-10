"""postinod-side audit helper.

Thin wrapper over `postino_core.audit` that namespaces actions under
`postinod.<resource>.<verb>` and stamps the `data` column with surface
metadata (which webhook delivered the event, which external_id it
referenced) so admins inspecting the PA `log` table can correlate a
mutation back to its IdP-side source event.

The helper participates in the caller's open transaction (the `conn`
parameter is the Connection inside an outer `engine.begin()`) so the
audit row commits atomically with the mutation it describes — no
mid-flight failure can leave a row that says "we did X" while the
underlying mutation rolled back, or vice versa.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import MetaData
from sqlalchemy.engine import Connection

from postino_core.audit import mk_postinod_action, write_audit


def write_postinod_audit(
    conn: Connection,
    md: MetaData,
    *,
    clock: Callable[[], datetime],
    resource: str,
    verb: str,
    domain: str,
    surface: str,
    external_id: str,
    payload: dict[str, str] | None = None,
) -> None:
    """Insert one `postinod.<resource>.<verb>` row into PA's `log` table.

    `surface` identifies the webhook (e.g. "zitadel", "scim").
    `external_id` is the IdP-side ID for the affected entity.
    `payload` is a small dict of human-readable fields (e.g. {"email": ...})
    serialized into the `data` column alongside the surface marker.
    """
    data = json.dumps(
        {
            "surface": surface,
            "external_id": external_id,
            "payload": payload or {},
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    write_audit(
        conn,
        md,
        clock=clock,
        action=mk_postinod_action(resource, verb),
        domain=domain,
        data=data,
    )
