"""POST /zitadel/events router.

Reads the raw body once, verifies HMAC inline (Litestar Guards drop the
receive channel — see auth/hmac_guard.py module docstring), parses with
`ZitadelEvent`, dispatches via the mapping table, calls the matching
`MailboxService` method, writes a `postinod.<resource>.<verb>` audit row
to PA's `log` table, and returns 200 OK on success or known no-op.

Error mapping (spec §4.5):
* HMAC mismatch / missing signature → 401
* ValidationError on the body → 400
* `NotFoundError` (e.g. unknown domain on CREATE) → 400
* `AlreadyExistsError` on CREATE → 200 OK (idempotent retry)
* `CapacityError` / `ConfigError` on any verb → 400
* Unknown event_type → 200 OK (IGNORE; spec lets Zitadel send anything)
* `user.human.email.changed` (REJECT outcome) → 400 (operator must
  use a different flow; out of scope for postinod V2)

Email-as-resolver (vjt 2026-05-11): the Zitadel Action target templates
`email` into every payload — including lifecycle events
(deactivated/reactivated/removed) and `profile.changed` which Zitadel
does not natively include. Without the template, postinod cannot
resolve the aggregate_id back to a mailbox username and would have to
maintain a local cache. The template eliminates that state. See
docs/postinod-deploy.md (Task 19) for the operator-side Action body.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

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
    NotFoundError,
)
from postino_core.models import MailboxCreate
from postino_core.services.mailbox import MailboxService
from postinod.audit import write_postinod_audit
from postinod.auth.hmac_guard import HmacVerifier
from postinod.zitadel.mapping import EventOutcome, dispatch_event
from postinod.zitadel.models import (
    HumanProfilePayload,
    LifecyclePayload,
    UserAddedPayload,
    ZitadelEvent,
)

_logger = logging.getLogger(__name__)


def build_zitadel_router(
    *,
    mailbox_service: MailboxService,
    hmac_verifier: HmacVerifier,
    engine: Engine,
    metadata: MetaData,
    clock: Callable[[], datetime],
    default_quota_bytes: int,
) -> Router:
    """Build the /zitadel/events sub-router.

    `engine` and `metadata` are injected separately (rather than reaching
    into `mailbox_service._engine` / `._md`) so the audit write opens its
    own transaction without depending on private MailboxService state.
    """

    def _audit(
        *,
        resource: str,
        verb: str,
        domain: str,
        external_id: str,
        payload: dict[str, str],
    ) -> None:
        with engine.begin() as conn:
            write_postinod_audit(
                conn,
                metadata,
                clock=clock,
                resource=resource,
                verb=verb,
                domain=domain,
                surface="zitadel",
                external_id=external_id,
                payload=payload,
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
            raise HTTPException(status_code=400, detail=str(e)) from e

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
            if outcome is EventOutcome.CREATE:
                _handle_create(
                    event=event,
                    mailbox_service=mailbox_service,
                    audit=_audit,
                    default_quota_bytes=default_quota_bytes,
                )
            elif outcome is EventOutcome.UPDATE:
                _handle_update(
                    event=event,
                    mailbox_service=mailbox_service,
                    audit=_audit,
                )
            elif outcome is EventOutcome.DISABLE:
                _handle_set_status(
                    event=event,
                    mailbox_service=mailbox_service,
                    audit=_audit,
                    status=MailboxStatus.DISABLED,
                    verb="disable",
                )
            elif outcome is EventOutcome.ENABLE:
                _handle_set_status(
                    event=event,
                    mailbox_service=mailbox_service,
                    audit=_audit,
                    status=MailboxStatus.ACTIVE,
                    verb="enable",
                )
        except AlreadyExistsError:
            return {"ok": True}
        except (NotFoundError, CapacityError, ConfigError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        return {"ok": True}

    return Router(path="/", route_handlers=[events])


class _AuditCallback(Protocol):
    """Callable that records a postinod audit row.

    Closes over `engine`, `metadata`, `clock`, and surface tag inside
    `build_zitadel_router`; the per-event handlers below only supply
    the resource/verb/domain/external_id/payload tuple.
    """

    def __call__(
        self,
        *,
        resource: str,
        verb: str,
        domain: str,
        external_id: str,
        payload: dict[str, str],
    ) -> None: ...


def _handle_create(
    *,
    event: ZitadelEvent,
    mailbox_service: MailboxService,
    audit: _AuditCallback,
    default_quota_bytes: int,
) -> None:
    payload = event.event_payload
    if not isinstance(payload, UserAddedPayload):
        raise HTTPException(
            status_code=400,
            detail="payload shape mismatch for user.human.added",
        )
    username = str(payload.email)
    _, _, domain = username.partition("@")
    mailbox_service.add(
        MailboxCreate(
            username=payload.email,
            name=f"{payload.first_name} {payload.last_name}".strip(),
            quota_bytes=default_quota_bytes,
        )
    )
    audit(
        resource="user",
        verb="create",
        domain=domain,
        external_id=event.user_id,
        payload={"email": username},
    )


def _handle_update(
    *,
    event: ZitadelEvent,
    mailbox_service: MailboxService,
    audit: _AuditCallback,
) -> None:
    payload = event.event_payload
    if not isinstance(payload, HumanProfilePayload):
        raise HTTPException(
            status_code=400,
            detail="payload shape mismatch for user.human.profile.changed",
        )
    username = str(payload.email)
    _, _, domain = username.partition("@")
    mailbox_service.set_name(payload.email, f"{payload.first_name} {payload.last_name}".strip())
    audit(
        resource="user",
        verb="update",
        domain=domain,
        external_id=event.user_id,
        payload={"email": username},
    )


def _handle_set_status(
    *,
    event: ZitadelEvent,
    mailbox_service: MailboxService,
    audit: _AuditCallback,
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
    _, _, domain = username.partition("@")
    mailbox_service.set_status(payload.email, status)
    audit(
        resource="user",
        verb=verb,
        domain=domain,
        external_id=event.user_id,
        payload={"email": username},
    )
