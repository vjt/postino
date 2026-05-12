"""POST /zitadel/events router.

Reads the raw body once, verifies HMAC inline (Litestar Guards drop the
receive channel — see auth/hmac_guard.py module docstring), parses with
`ZitadelEvent`, dispatches via the mapping table, and calls the matching
`MailboxService` method inside an `audit_context` that produces both a
`postino.<r>.<v>` and a `postinod.<r>.<v>` row in the same transaction.

Error mapping (spec §4.5):
* HMAC mismatch / missing signature → 401
* ValidationError on the body → 400
* `NotFoundError` (e.g. unknown domain on CREATE) → 400
* `AlreadyExistsError` on CREATE → 200 OK (idempotent retry)
* `CapacityError` / `ConfigError` on any verb → 400
* Unknown event_type → 200 OK (IGNORE; spec lets Zitadel send anything)
* `user.human.email.changed` (REJECT outcome) → 400 (operator must
  use a different flow; out of scope for postinod V2)

Email-as-resolver: the Zitadel Action target templates `email` into every
payload — including lifecycle events (deactivated/reactivated/removed) and
`profile.changed` which Zitadel does not natively include. Without the
template, postinod cannot resolve the aggregate_id back to a mailbox
username and would have to maintain a local cache. See
docs/postinod-deploy.md for the operator-side Action body.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime

import anyio.to_thread
from litestar import Request, Router, post
from litestar.datastructures import State
from litestar.exceptions import HTTPException
from litestar.status_codes import HTTP_200_OK
from pydantic import ValidationError
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

from postino_core.enums import MailboxStatus
from postino_core.errors import (
    AlreadyExistsError,
    CapacityError,
    ConfigError,
    DBError,
    FilesystemError,
    HookError,
    NotFoundError,
)
from postino_core.models import MailboxCreate
from postino_core.services.mailbox import MailboxService
from postinod.audit import PostinodAuditExtra, audit_context, write_postinod_audit
from postinod.auth.hmac_guard import HmacVerifier
from postinod.scim.errors import scim_validation_detail
from postinod.zitadel.mapping import EventOutcome, dispatch_event
from postinod.zitadel.models import (
    HumanProfilePayload,
    LifecyclePayload,
    UserAddedPayload,
    ZitadelEvent,
)

_logger = logging.getLogger(__name__)

# Bounded dedup cache for Zitadel event replay. Keyed on
# (user_id, event_type, created_at_iso). "Captured signed event
# replayed within the wall-clock window flips lifecycle state with no
# nonce-style dedup" closes here: any captured event recognised on a
# second delivery short-circuits to 200 OK with no mutation.
_SEEN_EVENT_CACHE_MAX = 8192

# Audit-data string cap for replay rows. Zitadel event_type is
# attacker-influenced (HMAC-authenticated but the secret-holder is the
# threat). Truncating bounds log-table inflation.
_AUDIT_FIELD_MAX_CHARS = 256


def _truncate(value: str, limit: int = _AUDIT_FIELD_MAX_CHARS) -> str:
    """Cap ``value`` at ``limit`` chars with a trailing marker so the
    audit row stays bounded under a hostile signed payload."""
    if len(value) <= limit:
        return value
    return value[:limit] + "…[truncated]"


def build_zitadel_router(
    *,
    mailbox_service: MailboxService,
    hmac_verifier: HmacVerifier,
    engine: Engine,
    metadata: MetaData,
    clock: Callable[[], datetime],
    default_quota_bytes: int,
    replay_window_seconds: int = 300,
) -> Router:
    """Build the /zitadel/events sub-router.

    Mutation-tied audit rows ride inside `mailbox_service`'s transaction
    via `PostinodAuditWriter` + the per-request `audit_context`
    contextvar. The side-channel `postinod.zitadel.replay` row (no
    corresponding mutation) still uses `write_postinod_audit` against the
    engine directly — that's why `engine` / `metadata` remain router
    parameters.

    `replay_window_seconds` rejects events whose `created_at` is outside
    [now - window, now + window]. A small clock skew either direction is
    tolerated; replay attacks past the window are rejected with 400 and
    a `postinod.zitadel.replay` audit row tagged with the event identifiers.

    Within the window, a bounded LRU dedup cache keyed on
    ``(user_id, event_type, created_at)`` collapses duplicate deliveries
    to 200 OK with no mutation and no additional audit row — closes the
    captured-signed-event replay path that wall-clock skew alone cannot.
    """

    # OrderedDict-backed FIFO/LRU. Key: deterministic event identity.
    # Value: True (we only care about membership). On insert past
    # _SEEN_EVENT_CACHE_MAX we evict the oldest entry — natural for the
    # delivery-order shape Zitadel produces.
    seen_events: OrderedDict[tuple[str, str, str], bool] = OrderedDict()

    def _seen_key(event: ZitadelEvent) -> tuple[str, str, str]:
        return (event.user_id, event.event_type, event.created_at.isoformat())

    def _replay_audit(*, external_id: str, event_type: str, skew: int) -> None:
        with engine.begin() as conn:
            write_postinod_audit(
                conn,
                metadata,
                clock=clock,
                resource="zitadel",
                verb="replay",
                domain="",
                surface="zitadel",
                external_id=_truncate(external_id),
                payload={
                    "event_type": _truncate(event_type),
                    "skew_sec": str(skew),
                },
            )

    def _extra(
        *,
        event: ZitadelEvent,
        username: str,
        postinod_action: tuple[str, str],
    ) -> PostinodAuditExtra:
        editor = _resolve_editor(event)
        return PostinodAuditExtra(
            surface="zitadel",
            external_id=event.user_id,
            payload={"email": username, "event_type": event.event_type},
            actor_resolver=lambda: editor,
            postinod_action=postinod_action,
        )

    @post("/zitadel/events", status_code=HTTP_200_OK)
    async def events(request: Request[None, None, State]) -> dict[str, bool]:
        body = await request.body()
        sig = request.headers.get(hmac_verifier.header_name)
        if not sig or not hmac_verifier.verify(body, sig):
            raise HTTPException(status_code=401, detail="invalid HMAC signature")

        # NOTE: parse via json.loads + model_validate so the custom
        # ZitadelEvent.model_validate override (which dispatches the
        # event_payload union by event_type) runs. model_validate_json
        # bypasses that override and falls back to plain pydantic union
        # resolution, which would coerce LifecyclePayload into
        # HumanEmailPayload (both have only `email`).
        try:
            raw = json.loads(body)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"invalid JSON: {e}") from e
        try:
            event = ZitadelEvent.model_validate(raw)
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=scim_validation_detail(e)) from e

        # Event-id dedup: a delivery we've already accepted within the
        # current cache window is idempotent. Short-circuit to 200 OK
        # without re-applying the mutation; the original audit row
        # already records the apply.
        key = _seen_key(event)
        if key in seen_events:
            seen_events.move_to_end(key)
            _logger.info(
                "deduped replayed event_type=%s user_id=%s",
                event.event_type,
                event.user_id,
            )
            return {"ok": True}

        skew = abs((clock() - event.created_at).total_seconds())
        if skew > replay_window_seconds:
            _logger.warning(
                "rejecting replayed event_type=%s user_id=%s skew=%ds",
                event.event_type,
                event.user_id,
                int(skew),
            )
            try:
                # Blocking SQL — offload to threadpool (A3-A3).
                await anyio.to_thread.run_sync(
                    lambda: _replay_audit(
                        external_id=event.user_id,
                        event_type=event.event_type,
                        skew=int(skew),
                    )
                )
            except Exception:
                # WHY: audit failure must not mask the replay rejection —
                # the 400 below is the primary signal; replay audit is best-effort.
                _logger.exception("failed to write replay audit row")
            raise HTTPException(
                status_code=400,
                detail=f"event outside replay window ({int(skew)}s > {replay_window_seconds}s)",
            )

        outcome = dispatch_event(event.event_type)

        if outcome is EventOutcome.IGNORE:
            _logger.info("ignored event_type=%s", event.event_type)
            return {"ok": True}

        if outcome is EventOutcome.REJECT:
            raise HTTPException(
                status_code=400,
                detail=f"event_type {event.event_type!r} not supported",
            )

        try:
            # Offload blocking SQL+FS+hook work to the threadpool so the
            # uvicorn event loop stays responsive (A3-A3). The
            # `_handle_*` helpers carry their own `audit_context`
            # block; anyio.to_thread.run_sync copies the current
            # contextvars snapshot into the worker.
            if outcome is EventOutcome.CREATE:
                await anyio.to_thread.run_sync(
                    lambda: _handle_create(
                        event=event,
                        mailbox_service=mailbox_service,
                        extra_for=_extra,
                        default_quota_bytes=default_quota_bytes,
                    )
                )
            elif outcome is EventOutcome.UPDATE:
                await anyio.to_thread.run_sync(
                    lambda: _handle_update(
                        event=event,
                        mailbox_service=mailbox_service,
                        extra_for=_extra,
                    )
                )
            elif outcome is EventOutcome.DISABLE:
                await anyio.to_thread.run_sync(
                    lambda: _handle_set_status(
                        event=event,
                        mailbox_service=mailbox_service,
                        extra_for=_extra,
                        status=MailboxStatus.DISABLED,
                        verb="disable",
                    )
                )
            elif outcome is EventOutcome.ENABLE:
                await anyio.to_thread.run_sync(
                    lambda: _handle_set_status(
                        event=event,
                        mailbox_service=mailbox_service,
                        extra_for=_extra,
                        status=MailboxStatus.ACTIVE,
                        verb="enable",
                    )
                )
        except AlreadyExistsError:
            seen_events[key] = True
            if len(seen_events) > _SEEN_EVENT_CACHE_MAX:
                seen_events.popitem(last=False)
            return {"ok": True}
        except (NotFoundError, CapacityError, ConfigError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except (DBError, FilesystemError, HookError) as e:
            # Internal mutator failures: log full detail server-side,
            # return a generic 500 so a privileged HMAC peer cannot probe
            # DBAPI / FS state via response bodies.
            _logger.exception(
                "internal failure processing event_type=%s user_id=%s",
                event.event_type,
                event.user_id,
            )
            raise HTTPException(status_code=500, detail="internal error") from e

        # Successful apply: remember the event so a re-delivery is a
        # cheap idempotent no-op.
        seen_events[key] = True
        if len(seen_events) > _SEEN_EVENT_CACHE_MAX:
            seen_events.popitem(last=False)
        return {"ok": True}

    return Router(path="/", route_handlers=[events])


_ExtraBuilder = Callable[..., PostinodAuditExtra]


def _resolve_editor(event: ZitadelEvent) -> str:
    """Identity recorded in `log.username` for postinod-attributed rows.

    Zitadel includes an `editor.userId` field on most events; postino
    does not currently parse it (ZitadelEvent has no editor field), so
    we fall back to `event.user_id` until the model captures it. Tracked
    as a small follow-up; the event_id is still a useful audit trail.
    """
    return f"zitadel:{event.user_id}"


def _handle_create(
    *,
    event: ZitadelEvent,
    mailbox_service: MailboxService,
    extra_for: _ExtraBuilder,
    default_quota_bytes: int,
) -> None:
    payload = event.event_payload
    if not isinstance(payload, UserAddedPayload):
        raise HTTPException(
            status_code=400,
            detail="payload shape mismatch for user.human.added",
        )
    username = str(payload.email)
    extra = extra_for(event=event, username=username, postinod_action=("user", "create"))
    with audit_context(extra):
        mailbox_service.add(
            MailboxCreate(
                username=payload.email,
                name=f"{payload.first_name} {payload.last_name}".strip(),
                quota_bytes=default_quota_bytes,
            )
        )


def _handle_update(
    *,
    event: ZitadelEvent,
    mailbox_service: MailboxService,
    extra_for: _ExtraBuilder,
) -> None:
    payload = event.event_payload
    if not isinstance(payload, HumanProfilePayload):
        raise HTTPException(
            status_code=400,
            detail="payload shape mismatch for user.human.profile.changed",
        )
    username = str(payload.email)
    extra = extra_for(event=event, username=username, postinod_action=("user", "update"))
    with audit_context(extra):
        mailbox_service.set_name(payload.email, f"{payload.first_name} {payload.last_name}".strip())


def _handle_set_status(
    *,
    event: ZitadelEvent,
    mailbox_service: MailboxService,
    extra_for: _ExtraBuilder,
    status: MailboxStatus,
    verb: str,
) -> None:
    payload = event.event_payload
    if not isinstance(payload, LifecyclePayload):
        raise HTTPException(
            status_code=400,
            detail=f"payload shape mismatch for lifecycle event ({verb})",
        )
    username = str(payload.email)
    extra = extra_for(event=event, username=username, postinod_action=("user", verb))
    with audit_context(extra):
        mailbox_service.set_status(payload.email, status)
