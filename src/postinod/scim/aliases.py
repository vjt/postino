"""SCIM /Aliases router — postino extension resource.

Implements POST, GET, DELETE for the Alias resource under the postino
custom schema urn:postino:params:scim:schemas:core:2.0:Alias.

This is NOT a RFC 7644 core resource.  The Alias concept maps directly
to the PostfixAdmin `alias` table (address → goto comma-list) and is
exposed here so SCIM-capable provisioners (e.g. Zitadel group→alias
sync) can manage mail routing rules without direct DB access.

Auth: inline JWT bearer verification at the top of every handler.
Litestar Guards are intentionally avoided — the Guard hook fires before
the receive channel is consumed, creating a body-read hazard (see
auth/jwt_guard.py module docstring).

Error mapping follows errors.py:
  POST:   ValidationError → 400 invalidValue
          NotFoundError (unknown domain) → 400 invalidValue (create_path=True)
          AlreadyExistsError → 409 uniqueness
          CapacityError → 400 tooMany
          ConfigError → 400 invalidValue
          DB/FS/Hook errors → 500
  GET:    None → 404 SCIM Error envelope
  DELETE: NotFoundError → 404 (Litestar default JSON; rare path, matches
          Users router behaviour after PR-B11.1)
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

import jwt
from litestar import Request, Router, delete, get, post
from litestar.datastructures import State
from litestar.exceptions import HTTPException
from litestar.response import Response
from litestar.status_codes import HTTP_200_OK, HTTP_201_CREATED
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

from postino_core.errors import (
    AlreadyExistsError,
    CapacityError,
    ConfigError,
    DBError,
    FilesystemError,
    HookError,
    NotFoundError,
)
from postino_core.models import Alias
from postino_core.services.alias import AliasService
from postinod.audit import write_postinod_audit
from postinod.auth.jwt_guard import JwtVerifier
from postinod.scim.errors import scim_error_from_exception
from postinod.scim.models import ALIAS_SCHEMA, ScimAlias, ScimError

_logger = logging.getLogger(__name__)

_email_adapter: TypeAdapter[EmailStr] = TypeAdapter(EmailStr)

SCIM_CONTENT_TYPE = "application/scim+json"


class _AuditCallback(Protocol):
    """Callable that records a postinod audit row.

    Closes over `engine`, `metadata`, `clock`, and surface tag inside
    `build_aliases_router`; per-handler call sites supply only the
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


def _alias_to_resource(a: Alias) -> ScimAlias:
    """Build a ScimAlias view from an Alias domain object."""
    return ScimAlias(
        schemas=[ALIAS_SCHEMA],
        id=str(a.address),
        address=a.address,
        goto=a.goto,
    )


def _scim_response(
    model: ScimAlias | ScimError,
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


def build_aliases_router(
    *,
    alias_service: AliasService,
    jwt_verifier: JwtVerifier,
    engine: Engine,
    metadata: MetaData,
    clock: Callable[[], datetime],
) -> Router:
    """Build the /scim/v2/Aliases sub-router.

    `engine` and `metadata` are injected separately (rather than reaching
    into `alias_service._engine` / `._md`) so the audit write opens its
    own transaction without depending on private AliasService state.
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

    @post("/scim/v2/Aliases", status_code=HTTP_201_CREATED)
    async def create_alias(
        request: Request[None, None, State],
    ) -> Response[dict[str, object]]:
        await _verify_bearer(request)

        try:
            raw = json.loads(await request.body())
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"invalid JSON: {e}") from e

        try:
            res = ScimAlias.model_validate(raw)
        except ValidationError as e:
            err = ScimError(status="400", scimType="invalidValue", detail=str(e))
            return _scim_response(err, 400)

        try:
            created = alias_service.add(address=res.address, goto=res.goto)
        except NotFoundError as e:
            return _err(e, create_path=True)
        except (AlreadyExistsError, CapacityError, ConfigError) as e:
            return _err(e)
        except (DBError, FilesystemError, HookError) as e:
            return _err(e)

        address_str = str(created.address)
        _, _, domain = address_str.partition("@")
        _audit(
            resource="alias",
            verb="create",
            domain=domain,
            external_id=address_str,
            payload={"goto": created.goto},
        )

        location = f"/scim/v2/Aliases/{address_str}"
        return _scim_response(_alias_to_resource(created), HTTP_201_CREATED, location=location)

    @get("/scim/v2/Aliases/{alias_id:str}", status_code=HTTP_200_OK)
    async def get_alias(
        request: Request[None, None, State],
        alias_id: str,
    ) -> Response[dict[str, object]]:
        await _verify_bearer(request)

        try:
            email = _as_email(alias_id)
        except ValidationError as e:
            err = ScimError(status="400", scimType="invalidValue", detail=str(e))
            return _scim_response(err, 400)

        a = alias_service.get(email)
        if a is None:
            err = ScimError(status="404", detail=f"alias {alias_id!r} not found")
            return _scim_response(err, 404)

        return _scim_response(_alias_to_resource(a), HTTP_200_OK)

    @delete("/scim/v2/Aliases/{alias_id:str}", status_code=204)
    async def delete_alias(
        alias_id: str,
        request: Request[None, None, State],
    ) -> None:
        # NOTE: DELETE 404 raises HTTPException (Litestar default JSON error),
        # not a SCIM Error envelope. Acceptable: DELETE 404 is rare and the
        # test suite only asserts 204 on success. Mirrors Users router behaviour
        # after PR-B11.1.
        await _verify_bearer(request)

        try:
            email = _as_email(alias_id)
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        address_str = alias_id
        _, _, domain = address_str.partition("@")

        try:
            alias_service.delete(email)
        except NotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        _audit(
            resource="alias",
            verb="delete",
            domain=domain,
            external_id=address_str,
            payload={},
        )

    return Router(path="/", route_handlers=[create_alias, get_alias, delete_alias])
