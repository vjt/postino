"""postinod audit machinery — atomic dual-row writer + side-channel helper.

The mutator services in postino_core write a `postino.<resource>.<verb>`
audit row inside their mutation transaction. When postinod handles the
mutation (SCIM or Zitadel-webhook driven), we also want a
`postinod.<resource>.<verb>` row in the **same** transaction so a crash
between the mutation and a separate audit-write cannot leave a row
saying "we did X" without a matching record from the daemon's surface.

`PostinodAuditWriter` is the AuditWriter implementation that satisfies
this: it writes BOTH the postino.* and the postinod.* rows on the same
Connection. The per-request surface metadata (which webhook delivered
the event, which IdP-side external_id it referenced, and a small
human-readable payload) flows through a contextvar set by the handler
before it calls the mutator service.

`write_postinod_audit` is retained for side-channel rows that don't
correspond to a postino_core mutation — currently only the
`postinod.zitadel.replay` row written when the events router rejects
a replayed event. Those open their own transaction.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import MetaData
from sqlalchemy.engine import Connection

from postino_core.audit import (
    POSTINOD_ACTION_PREFIX,
    DefaultAuditWriter,
    default_actor,
    mk_postinod_action,
    write_audit,
)


@dataclass(frozen=True)
class PostinodAuditExtra:
    """Per-request audit context written by the route handler.

    `surface` identifies the webhook ("zitadel", "scim").
    `external_id` is the IdP-side ID for the affected entity.
    `payload` is a small dict of human-readable fields (e.g.
    {"email": ...}) JSON-serialized into the `data` column.
    `actor_resolver` resolves the daemon-side identity recorded in
    `log.username` for both rows; the handler builds it from the SCIM
    JWT `sub` claim or the Zitadel event `editor.userId`.
    `postinod_action` overrides the postinod.* mirror row's action when
    the surface-facing verb differs from the mutator-side verb (e.g. a
    SCIM `user.disable` maps to a `mailbox.set_status` mutation). When
    None, the mirror row reuses the mutator's resource/verb.
    """

    surface: str
    external_id: str
    payload: dict[str, str] = field(default_factory=lambda: {})
    actor_resolver: Callable[[], str] = default_actor
    postinod_action: tuple[str, str] | None = None


_extra_var: contextvars.ContextVar[PostinodAuditExtra | None] = contextvars.ContextVar(
    "postinod_audit_extra", default=None
)


def set_audit_extra(
    extra: PostinodAuditExtra | None,
) -> contextvars.Token[PostinodAuditExtra | None]:
    """Install per-request audit context. Returns the reset token.

    Call at the top of every postinod handler that drives a mutator;
    pair with `reset_audit_extra(token)` in a try/finally."""
    return _extra_var.set(extra)


def reset_audit_extra(token: contextvars.Token[PostinodAuditExtra | None]) -> None:
    _extra_var.reset(token)


def current_audit_extra() -> PostinodAuditExtra | None:
    """Read the current PostinodAuditExtra (or None outside a postinod request)."""
    return _extra_var.get()


@contextlib.contextmanager
def audit_context(extra: PostinodAuditExtra) -> Generator[None, None, None]:
    """Context manager that installs `extra` for the duration of the block.

    Convenience over `set_audit_extra` / `reset_audit_extra`. Use:

        with audit_context(extra):
            mailbox_service.add(...)
    """
    token = set_audit_extra(extra)
    try:
        yield
    finally:
        reset_audit_extra(token)


@dataclass(frozen=True)
class PostinodAuditWriter:
    """AuditWriter that emits BOTH `postino.<r>.<v>` and `postinod.<r>.<v>`
    rows on the same Connection.

    Reads per-request surface metadata from the `postinod_audit_extra`
    contextvar set by the handler. Falls back to a default surface tag
    if no extra is set, so unexpected call sites still produce a row
    rather than crashing the mutation.
    """

    metadata: MetaData
    clock: Callable[[], datetime]
    fallback_surface: str = "unknown"

    def write(
        self,
        conn: Connection,
        *,
        action: str,
        domain: str,
        data: str,
    ) -> None:
        extra = _extra_var.get()
        actor: Callable[[], str] = extra.actor_resolver if extra is not None else default_actor

        # 1) postino.<r>.<v> row — mirrors the CLI's audit so PA web UI
        #    sees daemon-driven mutations alongside CLI ones.
        DefaultAuditWriter(metadata=self.metadata, clock=self.clock, actor=actor).write(
            conn, action=action, domain=domain, data=data
        )

        # 2) postinod.<r>.<v> row — daemon-attribution mirror.
        if extra is not None and extra.postinod_action is not None:
            res, verb = extra.postinod_action
            action_postinod = mk_postinod_action(res, verb)
        else:
            action_postinod = _postinod_action_from(action)
        payload_dict: dict[str, str] = {}
        if extra is not None:
            payload_dict = dict(extra.payload)
        data_postinod = json.dumps(
            {
                "surface": extra.surface if extra is not None else self.fallback_surface,
                "external_id": extra.external_id if extra is not None else "",
                "payload": payload_dict,
                "data": data,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        write_audit(
            conn,
            self.metadata,
            clock=self.clock,
            action=action_postinod,
            domain=domain,
            data=data_postinod,
            actor=actor,
        )


def _postinod_action_from(action: str) -> str:
    """Map `postino.<r>.<v>` → `postinod.<r>.<v>` for the mirror row.

    Falls back to namespacing unrecognized inputs under the postinod
    prefix so a future verb-prefix shift doesn't break atomicity.
    """
    if action.startswith("postino."):
        return f"{POSTINOD_ACTION_PREFIX}.{action[len('postino.') :]}"
    return f"{POSTINOD_ACTION_PREFIX}.{action}"


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
    actor: Callable[[], str] = default_actor,
) -> None:
    """Side-channel single-row writer for postinod-only audit entries.

    Used for events that don't correspond to a postino_core mutation —
    notably `postinod.zitadel.replay` when the events router rejects
    a replayed/skewed event. Mutation-tied audit rows MUST go through
    `PostinodAuditWriter` instead so they join the same transaction.
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
        actor=actor,
    )
