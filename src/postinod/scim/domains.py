"""SCIM /Domains router — postino extension resource (read-only).

Implements GET list + GET single under the postino custom schema
``urn:postino:params:scim:schemas:core:2.0:Domain``.

This is NOT a RFC 7644 core resource. The Domain concept maps to the
PostfixAdmin ``domain`` table and is exposed here so SCIM-capable
provisioners can enumerate the deployment's tenant domains without
direct DB access. Mutating verbs are intentionally not implemented:
domain provisioning is an operator concern (CLI / postino.toml), not
an IdP-driven flow.

Auth: inline JWT bearer verification at the top of every handler
(matches Users / Aliases routers; avoids the body-receive-channel
hazard documented in auth/jwt_guard.py).
"""

from __future__ import annotations

import logging

import jwt
from litestar import Request, Router, get
from litestar.datastructures import State
from litestar.exceptions import HTTPException
from litestar.response import Response
from litestar.status_codes import HTTP_200_OK

from postino_core.models import Domain
from postino_core.services.domain import DomainService
from postinod.auth.jwt_guard import JwtVerifier
from postinod.scim.models import (
    DOMAIN_SCHEMA,
    ScimDomain,
    ScimError,
    ScimListResponse,
)
from postinod.scim.query import (
    InvalidFilterError,
    ListQuery,
    parse_list_query,
)

_logger = logging.getLogger(__name__)

SCIM_CONTENT_TYPE = "application/scim+json"


def _domain_to_resource(d: Domain) -> ScimDomain:
    """Build a ScimDomain view from a Domain domain object."""
    return ScimDomain(
        schemas=[DOMAIN_SCHEMA],
        id=d.domain,
        domain=d.domain,
        description=d.description,
        transport=d.transport.value,
        maxAliases=d.max_aliases,
        maxMailboxes=d.max_mailboxes,
        maxQuotaBytes=d.max_quota_bytes,
        defaultQuotaBytes=d.default_quota_bytes,
        backupmx=d.backupmx,
        active=(int(d.status) == 1),
    )


def _scim_response(
    model: ScimDomain | ScimError,
    status: int,
) -> Response[dict[str, object]]:
    """Render a SCIM model as a Litestar Response."""
    body = model.model_dump(by_alias=True, exclude_none=True)
    return Response(
        content=body,
        status_code=status,
        media_type=SCIM_CONTENT_TYPE,
    )


def build_domains_router(
    *,
    domain_service: DomainService,
    jwt_verifier: JwtVerifier,
) -> Router:
    """Build the /scim/v2/Domains sub-router (list + single GET, read-only)."""

    async def _verify_bearer(request: Request[None, None, State]) -> None:
        auth = request.headers.get("Authorization")
        if not auth or not auth.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        token = auth.removeprefix("Bearer ").strip()
        try:
            await jwt_verifier.verify(token)
        except (jwt.InvalidTokenError, jwt.InvalidKeyError, KeyError):
            raise HTTPException(status_code=401, detail="invalid bearer token") from None

    @get("/scim/v2/Domains", status_code=HTTP_200_OK)
    async def list_domains(
        request: Request[None, None, State],
        startIndex: int | None = None,
        count: int | None = None,
        filter: str | None = None,
    ) -> Response[dict[str, object]]:
        await _verify_bearer(request)

        try:
            q = parse_list_query(start_index=startIndex, count=count, filter_expr=filter)
        except InvalidFilterError as e:
            err = ScimError(status="400", scimType="invalidFilter", detail=str(e))
            return _scim_response(err, 400)

        all_rows = _resolve_domains(domain_service, q)
        page = all_rows[q.start_index - 1 : q.start_index - 1 + q.count]
        envelope = ScimListResponse(
            totalResults=len(all_rows),
            itemsPerPage=len(page),
            startIndex=q.start_index,
            Resources=[
                _domain_to_resource(d).model_dump(by_alias=True, exclude_none=True) for d in page
            ],
        )
        return Response(
            content=envelope.model_dump(by_alias=True, exclude_none=True),
            status_code=HTTP_200_OK,
            media_type=SCIM_CONTENT_TYPE,
        )

    @get("/scim/v2/Domains/{domain_id:str}", status_code=HTTP_200_OK)
    async def get_domain(
        request: Request[None, None, State],
        domain_id: str,
    ) -> Response[dict[str, object]]:
        await _verify_bearer(request)

        d = domain_service.get(domain_id)
        if d is None:
            err = ScimError(status="404", detail=f"domain {domain_id!r} not found")
            return _scim_response(err, 404)

        return _scim_response(_domain_to_resource(d), HTTP_200_OK)

    return Router(path="/", route_handlers=[list_domains, get_domain])


def _resolve_domains(domain_service: DomainService, q: ListQuery) -> list[Domain]:
    """Apply a parsed `ListQuery` to DomainService.

    Filter axes:
      * `domain eq "<fqdn>"` — single-row lookup via `get`.
    Anything else returns the full set.
    """
    if q.filter_attr == "domain" and q.filter_value is not None:
        d = domain_service.get(q.filter_value)
        return [d] if d is not None else []
    return domain_service.list()
