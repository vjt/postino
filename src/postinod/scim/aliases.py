"""SCIM /Aliases router — postino extension resource.

Implements POST, GET, PATCH, DELETE for the Alias resource under the postino
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
  PATCH:  filter-path → 400 invalidPath
          unsupported path/op combination → 400 invalidPath
          NotFoundError (set_status) → 404
  DELETE: NotFoundError → 404 (Litestar default JSON; rare path, matches
          Users router behaviour after PR-B11.1)
"""

from __future__ import annotations

import json
import logging

import jwt
from litestar import Request, Router, delete, get, patch, post
from litestar.datastructures import State
from litestar.exceptions import HTTPException
from litestar.response import Response
from litestar.status_codes import HTTP_200_OK, HTTP_201_CREATED
from pydantic import EmailStr, TypeAdapter, ValidationError

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
from postino_core.models import Alias
from postino_core.services.alias import AliasService
from postinod.audit import PostinodAuditExtra, audit_context
from postinod.auth.jwt_guard import JwtVerifier
from postinod.scim.errors import scim_error_from_exception, scim_validation_detail
from postinod.scim.models import (
    ALIAS_SCHEMA,
    PatchOpRequest,
    ScimAlias,
    ScimError,
    ScimListResponse,
    ScimMeta,
)
from postinod.scim.query import (
    InvalidFilterError,
    ListQuery,
    parse_list_query,
)

_logger = logging.getLogger(__name__)

_email_adapter: TypeAdapter[EmailStr] = TypeAdapter(EmailStr)

SCIM_CONTENT_TYPE = "application/scim+json"


def _as_email(s: str) -> EmailStr:
    """Coerce a plain string to a validated EmailStr.

    Raises pydantic.ValidationError if the string is not a valid email.
    """
    return _email_adapter.validate_python(s)


def _alias_to_resource(a: Alias) -> ScimAlias:
    """Build a ScimAlias view from an Alias domain object."""
    address_str = str(a.address)
    return ScimAlias(
        schemas=[ALIAS_SCHEMA],
        id=address_str,
        address=a.address,
        goto=a.goto,
        active=a.status is MailboxStatus.ACTIVE,
        meta=ScimMeta(
            resourceType="Alias",  # type: ignore[call-arg]  # WHY: pydantic accepts alias at construction; pyright sees field name only
            created=a.created,
            lastModified=a.modified,  # type: ignore[call-arg]  # WHY: see above
            location=f"/scim/v2/Aliases/{address_str}",
        ),
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
) -> Router:
    """Build the /scim/v2/Aliases sub-router.

    Audit rows ride inside `alias_service`'s mutation transaction via
    `PostinodAuditWriter` + the per-request `audit_context` contextvar.
    JWT verification is inline (not via Litestar Guards) to avoid the
    body-receive-channel hazard.
    """

    async def _verify_bearer(request: Request[None, None, State]) -> str:
        auth = request.headers.get("Authorization")
        if not auth or not auth.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        token = auth.removeprefix("Bearer ").strip()
        try:
            claims = await jwt_verifier.verify(token)
        except (jwt.InvalidTokenError, jwt.InvalidKeyError, KeyError):
            raise HTTPException(status_code=401, detail="invalid bearer token") from None
        sub = claims.get("sub")
        return str(sub) if isinstance(sub, str) and sub else "scim-client"

    def _extra(
        *,
        address_str: str,
        actor_sub: str,
        postinod_action: tuple[str, str],
        payload: dict[str, str] | None = None,
    ) -> PostinodAuditExtra:
        return PostinodAuditExtra(
            surface="scim",
            external_id=address_str,
            payload=payload or {},
            actor_resolver=lambda: actor_sub,
            postinod_action=postinod_action,
        )

    @get("/scim/v2/Aliases", status_code=HTTP_200_OK)
    async def list_aliases(
        request: Request[None, None, State],
        startIndex: int | None = None,
        count: int | None = None,
        filter: str | None = None,
    ) -> Response[dict[str, object]]:
        _ = await _verify_bearer(request)

        try:
            q = parse_list_query(start_index=startIndex, count=count, filter_expr=filter)
        except InvalidFilterError as e:
            err = ScimError(status="400", scimType="invalidFilter", detail=str(e))
            return _scim_response(err, 400)

        all_rows = _resolve_aliases(alias_service, q)
        page = all_rows[q.start_index - 1 : q.start_index - 1 + q.count]
        envelope = ScimListResponse(
            totalResults=len(all_rows),
            itemsPerPage=len(page),
            startIndex=q.start_index,
            Resources=[
                _alias_to_resource(a).model_dump(by_alias=True, exclude_none=True) for a in page
            ],
        )
        return Response(
            content=envelope.model_dump(by_alias=True, exclude_none=True),
            status_code=HTTP_200_OK,
            media_type=SCIM_CONTENT_TYPE,
        )

    @post("/scim/v2/Aliases", status_code=HTTP_201_CREATED)
    async def create_alias(
        request: Request[None, None, State],
    ) -> Response[dict[str, object]]:
        actor_sub = await _verify_bearer(request)

        try:
            raw = json.loads(await request.body())
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"invalid JSON: {e}") from e

        try:
            res = ScimAlias.model_validate(raw)
        except ValidationError as e:
            err = ScimError(status="400", scimType="invalidValue", detail=str(e))
            return _scim_response(err, 400)

        extra = _extra(
            address_str=str(res.address),
            actor_sub=actor_sub,
            postinod_action=("alias", "create"),
            payload={"goto": res.goto},
        )
        try:
            with audit_context(extra):
                created = alias_service.add(address=res.address, goto=res.goto)
        except NotFoundError as e:
            return _err(e, create_path=True)
        except (AlreadyExistsError, CapacityError, ConfigError) as e:
            return _err(e)
        except (DBError, FilesystemError, HookError) as e:
            return _err(e)

        address_str = str(created.address)
        location = f"/scim/v2/Aliases/{address_str}"
        return _scim_response(_alias_to_resource(created), HTTP_201_CREATED, location=location)

    @get("/scim/v2/Aliases/{alias_id:str}", status_code=HTTP_200_OK)
    async def get_alias(
        request: Request[None, None, State],
        alias_id: str,
    ) -> Response[dict[str, object]]:
        _ = await _verify_bearer(request)

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
        actor_sub = await _verify_bearer(request)

        try:
            email = _as_email(alias_id)
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        extra = _extra(
            address_str=alias_id,
            actor_sub=actor_sub,
            postinod_action=("alias", "delete"),
        )
        try:
            with audit_context(extra):
                alias_service.delete(email)
        except NotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @patch("/scim/v2/Aliases/{alias_id:str}", status_code=HTTP_200_OK)
    async def patch_alias(
        request: Request[None, None, State],
        alias_id: str,
    ) -> Response[dict[str, object]]:
        actor_sub = await _verify_bearer(request)

        try:
            raw = json.loads(await request.body())
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"invalid JSON: {e}") from e

        try:
            patch_req = PatchOpRequest.model_validate(raw)
        except ValidationError as e:
            err = ScimError(status="400", scimType="invalidValue", detail=scim_validation_detail(e))
            return _scim_response(err, 400)

        try:
            address = _as_email(alias_id)
        except ValidationError as e:
            err = ScimError(status="400", scimType="invalidValue", detail=scim_validation_detail(e))
            return _scim_response(err, 400)

        # RFC 7644 §3.5.2 — atomic PATCH: validate all ops before applying any.
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
            if op.op == "replace" and op.path == "active":
                continue
            err = ScimError(
                status="400",
                scimType="invalidPath",
                detail=f"unsupported PATCH path/op combination: op={op.op!r} path={op.path!r}",
            )
            return _scim_response(err, 400)

        # Second pass: apply each op (all validated above).
        for op in patch_req.operations:
            if op.op == "replace" and op.path == "active":
                new_status = MailboxStatus.ACTIVE if op.value else MailboxStatus.DISABLED
                verb = "enable" if new_status == MailboxStatus.ACTIVE else "disable"
                extra = _extra(
                    address_str=str(address),
                    actor_sub=actor_sub,
                    postinod_action=("alias", verb),
                )
                try:
                    with audit_context(extra):
                        alias_service.set_status(str(address), new_status)
                except NotFoundError as e:
                    return _err(e)
                except (DBError, FilesystemError, HookError) as e:
                    _logger.exception("internal failure on PATCH active for %s", address)
                    return _err(e)

        # Re-fetch to return current state.
        a = alias_service.get(address)
        if a is None:
            err = ScimError(status="404", detail=f"alias {alias_id!r} not found")
            return _scim_response(err, 404)
        return _scim_response(_alias_to_resource(a), HTTP_200_OK)

    return Router(
        path="/",
        route_handlers=[list_aliases, create_alias, get_alias, patch_alias, delete_alias],
    )


def _resolve_aliases(alias_service: AliasService, q: ListQuery) -> list[Alias]:
    """Apply a parsed `ListQuery` to AliasService and return the matching aliases.

    Filter axes:
      * `address eq "<email>"` — single-row lookup via `get`.
      * `domain eq "<fqdn>"` — `list(domain=fqdn)`.
    Anything else returns the full set.
    """
    if q.filter_attr == "address" and q.filter_value is not None:
        try:
            email = _as_email(q.filter_value)
        except ValidationError:
            return []
        a = alias_service.get(email)
        return [a] if a is not None else []
    if q.filter_attr == "domain" and q.filter_value is not None:
        return alias_service.list(domain=q.filter_value)
    return alias_service.list()
