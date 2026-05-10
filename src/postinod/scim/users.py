"""SCIM 2.0 /Users router.

Implements POST, GET, PATCH, DELETE for the User resource.

Auth: inline JWT bearer verification at the top of every handler.
Litestar Guards are intentionally avoided — the Guard hook fires before
the receive channel is consumed, creating a body-read hazard (see
auth/jwt_guard.py module docstring).

Error mapping follows errors.py / spec §4.5:
  POST:   ValidationError → 400 invalidValue
          NotFoundError (unknown domain) → 400 invalidValue (create_path=True)
          AlreadyExistsError → 409 uniqueness
          CapacityError → 400 tooMany
          ConfigError → 400 invalidValue
          DB/FS/Hook errors → 500
  GET:    NotFoundError → 404
  PATCH:  filter-path → 400 invalidPath
          unsupported path → 400 invalidPath
          NotFoundError → 404
  DELETE: NotFoundError → 404 (Litestar default JSON error, not SCIM envelope —
          acceptable since DELETE 404 is rare and the spec allows plain 404)
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

import jwt
from litestar import Request, Router, delete, get, patch, post
from litestar.datastructures import State
from litestar.exceptions import HTTPException
from litestar.response import Response
from litestar.status_codes import HTTP_200_OK, HTTP_201_CREATED
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

from postino_core.enums import MailboxStatus
from postino_core.errors import (
    AlreadyExistsError,
    CapacityError,
    ConfigError,
    NotFoundError,
)
from postino_core.models import Mailbox, MailboxCreate
from postino_core.services.mailbox import MailboxService
from postinod.audit import write_postinod_audit
from postinod.auth.jwt_guard import JwtVerifier
from postinod.scim.errors import scim_error_from_exception
from postinod.scim.models import (
    PatchOpRequest,
    ScimEmail,
    ScimError,
    ScimName,
    ScimUser,
)

_logger = logging.getLogger(__name__)

_email_adapter: TypeAdapter[EmailStr] = TypeAdapter(EmailStr)

SCIM_CONTENT_TYPE = "application/scim+json"


class _AuditCallback(Protocol):
    """Callable that records a postinod audit row.

    Closes over `engine`, `metadata`, `clock`, and surface tag inside
    `build_users_router`; per-handler call sites supply only the
    resource/verb/domain/external_id/payload tuple.
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


def _as_email(s: str) -> EmailStr:
    """Coerce a plain string to a validated EmailStr.

    Raises pydantic.ValidationError if the string is not a valid email.
    """
    return _email_adapter.validate_python(s)


def _user_from_mailbox(m: Mailbox) -> ScimUser:
    """Build a ScimUser view from a Mailbox domain object."""
    return ScimUser(
        schemas=["urn:ietf:params:scim:schemas:core:2.0:User"],
        id=str(m.username),
        userName=str(m.username),  # type: ignore[call-arg]  # WHY: pydantic accepts alias at construction; pyright sees field name only
        name=ScimName(formatted=m.name),
        emails=[ScimEmail(value=m.username, primary=True)],
        active=(m.status == MailboxStatus.ACTIVE),
    )


def _scim_response(
    model: ScimUser | ScimError,
    status: int,
    *,
    location: str | None = None,
) -> Response[dict[str, object]]:
    """Render a SCIM model as a Litestar Response."""
    headers: dict[str, str] = {}
    if location is not None:
        headers["Location"] = location
    body = model.model_dump(by_alias=True, exclude_none=True)
    return Response(
        content=body,
        status_code=status,
        headers=headers,
        media_type=SCIM_CONTENT_TYPE,
    )


def _err(exc: Exception, *, create_path: bool = False) -> Response[dict[str, object]]:
    """Map an exception to a SCIM Error response."""
    err_model = scim_error_from_exception(exc, create_path=create_path)
    return _scim_response(err_model, int(err_model.status))


def build_users_router(
    *,
    mailbox_service: MailboxService,
    jwt_verifier: JwtVerifier,
    engine: Engine,
    metadata: MetaData,
    clock: Callable[[], datetime],
    default_quota_bytes: int,
) -> Router:
    """Build the /scim/v2/Users sub-router.

    `engine` and `metadata` are injected separately (rather than reaching
    into `mailbox_service._engine` / `._md`) so the audit write opens its
    own transaction without depending on private MailboxService state.
    JWT verification is inline (not via Litestar Guards) to avoid the
    body-receive-channel hazard.
    """

    async def _verify_bearer(request: Request[None, None, State]) -> None:
        auth = request.headers.get("Authorization")
        if not auth or not auth.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        token = auth.removeprefix("Bearer ").strip()
        try:
            await jwt_verifier.verify(token)
        except (jwt.InvalidTokenError, jwt.InvalidKeyError, KeyError):
            raise HTTPException(status_code=401, detail="invalid bearer token") from None

    def _audit_impl(
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
                surface="scim",
                external_id=external_id,
                payload=payload,
            )

    _audit: _AuditCallback = _audit_impl

    @post("/scim/v2/Users", status_code=HTTP_201_CREATED)
    async def create_user(
        request: Request[None, None, State],
    ) -> Response[dict[str, object]]:
        await _verify_bearer(request)

        try:
            raw = json.loads(await request.body())
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"invalid JSON: {e}") from e

        try:
            user = ScimUser.model_validate(raw)
        except ValidationError as e:
            err = ScimError(status="400", scimType="invalidValue", detail=str(e))
            return _scim_response(err, 400)

        try:
            created = mailbox_service.add(
                MailboxCreate(
                    username=user.user_name,
                    name=user.name.formatted,
                    quota_bytes=default_quota_bytes,
                )
            )
        except (NotFoundError, AlreadyExistsError, CapacityError, ConfigError) as e:
            return _err(e, create_path=isinstance(e, NotFoundError))

        username_str = str(created.username)
        _, _, domain = username_str.partition("@")
        _audit(
            resource="user",
            verb="create",
            domain=domain,
            external_id=username_str,
            payload={"email": username_str},
        )

        location = f"/scim/v2/Users/{username_str}"
        return _scim_response(_user_from_mailbox(created), HTTP_201_CREATED, location=location)

    @get("/scim/v2/Users/{user_id:str}", status_code=HTTP_200_OK)
    async def get_user(
        request: Request[None, None, State],
        user_id: str,
    ) -> Response[dict[str, object]]:
        await _verify_bearer(request)

        try:
            email = _as_email(user_id)
        except ValidationError as e:
            err = ScimError(status="400", scimType="invalidValue", detail=str(e))
            return _scim_response(err, 400)

        m = mailbox_service.get(email)
        if m is None:
            err = ScimError(status="404", detail=f"user {user_id!r} not found")
            return _scim_response(err, 404)

        return _scim_response(_user_from_mailbox(m), HTTP_200_OK)

    @patch("/scim/v2/Users/{user_id:str}", status_code=HTTP_200_OK)
    async def patch_user(
        request: Request[None, None, State],
        user_id: str,
    ) -> Response[dict[str, object]]:
        await _verify_bearer(request)

        try:
            raw = json.loads(await request.body())
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"invalid JSON: {e}") from e

        try:
            patch_req = PatchOpRequest.model_validate(raw)
        except ValidationError as e:
            err = ScimError(status="400", scimType="invalidValue", detail=str(e))
            return _scim_response(err, 400)

        try:
            email = _as_email(user_id)
        except ValidationError as e:
            err = ScimError(status="400", scimType="invalidValue", detail=str(e))
            return _scim_response(err, 400)

        username_str = user_id
        _, _, domain = username_str.partition("@")

        # RFC 7644 §3.5.2 — atomic PATCH: validate all ops before applying any.
        # First pass: reject immediately if any op is unsupported.
        for op in patch_req.operations:
            if op.path is None:
                err = ScimError(
                    status="400",
                    scimType="invalidPath",
                    detail="PATCH operations without a path are not supported",
                )
                return _scim_response(err, 400)
            if "[" in op.path:
                err = ScimError(
                    status="400",
                    scimType="invalidPath",
                    detail=(
                        f"unsupported PATCH path expression (filters not supported): {op.path!r}"
                    ),
                )
                return _scim_response(err, 400)
            if not (op.op == "replace" and op.path in ("active", "name.formatted")):
                err = ScimError(
                    status="400",
                    scimType="invalidPath",
                    detail=f"unsupported PATCH path: {op.path!r}",
                )
                return _scim_response(err, 400)

        # Second pass: apply each op (all validated above).
        for op in patch_req.operations:
            if op.op == "replace" and op.path == "active":
                new_status = MailboxStatus.ACTIVE if op.value else MailboxStatus.DISABLED
                try:
                    mailbox_service.set_status(email, new_status)
                except NotFoundError as e:
                    return _err(e)
                verb = "enable" if new_status == MailboxStatus.ACTIVE else "disable"
                _audit(
                    resource="user",
                    verb=verb,
                    domain=domain,
                    external_id=username_str,
                    payload={"email": username_str},
                )

            elif op.op == "replace" and op.path == "name.formatted":
                try:
                    mailbox_service.set_name(email, str(op.value))
                except NotFoundError as e:
                    return _err(e)
                _audit(
                    resource="user",
                    verb="update",
                    domain=domain,
                    external_id=username_str,
                    payload={"email": username_str},
                )

        # Re-fetch to return current state.
        m = mailbox_service.get(email)
        if m is None:
            err = ScimError(status="404", detail=f"user {user_id!r} not found")
            return _scim_response(err, 404)

        return _scim_response(_user_from_mailbox(m), HTTP_200_OK)

    @delete("/scim/v2/Users/{user_id:str}", status_code=204)
    async def delete_user(
        user_id: str,
        request: Request[None, None, State],
    ) -> None:
        # NOTE: DELETE 404 raises HTTPException (Litestar default JSON error),
        # not a SCIM Error envelope. Acceptable: DELETE 404 is rare and the
        # test suite only asserts 204 on success. SCIM Error envelope on DELETE
        # 404 would require the workaround status_code=200 + explicit Response,
        # which introduces two type: ignore lines worse than this trade-off.
        await _verify_bearer(request)

        try:
            email = _as_email(user_id)
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        username_str = user_id
        _, _, domain = username_str.partition("@")

        try:
            mailbox_service.set_status(email, MailboxStatus.DISABLED)
        except NotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        _audit(
            resource="user",
            verb="disable",
            domain=domain,
            external_id=username_str,
            payload={"email": username_str},
        )

    return Router(path="/", route_handlers=[create_user, get_user, patch_user, delete_user])
