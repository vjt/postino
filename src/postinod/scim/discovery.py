"""SCIM 2.0 discovery endpoints (RFC 7644 §4).

Three GET endpoints surface server capabilities:
* /scim/v2/ServiceProviderConfig — supported features (patch, bulk, etc.)
* /scim/v2/ResourceTypes — list of resource types we expose
* /scim/v2/Schemas — schema definitions for those resource types

If `jwt_verifier` is None the endpoints are unauthenticated (suitable
for unit tests and IdP probes that hit discovery without auth). When
wired in production (Task 15) the JWT verifier is supplied and
inline-verifies the bearer token per RFC 7644 §3.5.
"""

from __future__ import annotations

import jwt
from litestar import Request, Router, get
from litestar.datastructures import State
from litestar.exceptions import HTTPException

from postinod.auth.jwt_guard import JwtVerifier

_SPC: dict[str, object] = {
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
    "patch": {"supported": True},
    "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
    "filter": {"supported": True, "maxResults": 200},
    "changePassword": {"supported": False},
    "sort": {"supported": False},
    "etag": {"supported": False},
    "authenticationSchemes": [
        {
            "type": "oauthbearertoken",
            "name": "OAuth Bearer Token",
            "description": "JWT signed by configured IdP",
            "primary": True,
        },
    ],
}

_RESOURCE_TYPES: dict[str, object] = {
    "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
    "totalResults": 2,
    "Resources": [
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
            "id": "User",
            "name": "User",
            "endpoint": "/Users",
            "schema": "urn:ietf:params:scim:schemas:core:2.0:User",
        },
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
            "id": "Alias",
            "name": "Alias",
            "endpoint": "/Aliases",
            "schema": "urn:postino:params:scim:schemas:core:2.0:Alias",
        },
    ],
}

_SCHEMAS: dict[str, object] = {
    "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
    "totalResults": 2,
    "Resources": [
        {
            "id": "urn:ietf:params:scim:schemas:core:2.0:User",
            "name": "User",
            "attributes": [
                {"name": "userName", "type": "string", "required": True, "uniqueness": "server"},
                {
                    "name": "name",
                    "type": "complex",
                    "subAttributes": [{"name": "formatted", "type": "string"}],
                },
                {"name": "active", "type": "boolean"},
                {"name": "emails", "type": "complex", "multiValued": True},
            ],
        },
        {
            "id": "urn:postino:params:scim:schemas:core:2.0:Alias",
            "name": "Alias",
            "attributes": [
                {"name": "address", "type": "string", "required": True, "uniqueness": "server"},
                {"name": "goto", "type": "string", "required": True},
            ],
        },
    ],
}


def build_discovery_router(*, jwt_verifier: JwtVerifier | None) -> Router:
    """Build the SCIM discovery sub-router.

    If `jwt_verifier` is provided, every handler inline-verifies the
    bearer token before returning the static metadata. If `None`, the
    endpoints are unauthenticated.
    """

    async def _maybe_verify(request: Request[None, None, State]) -> None:
        if jwt_verifier is None:
            return
        auth = request.headers.get("Authorization")
        if not auth or not auth.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        token = auth.removeprefix("Bearer ").strip()
        try:
            await jwt_verifier.verify(token)
        except (jwt.InvalidTokenError, jwt.InvalidKeyError, KeyError):
            raise HTTPException(status_code=401, detail="invalid bearer token") from None

    @get("/scim/v2/ServiceProviderConfig")
    async def service_provider_config(
        request: Request[None, None, State],
    ) -> dict[str, object]:
        await _maybe_verify(request)
        return _SPC

    @get("/scim/v2/ResourceTypes")
    async def resource_types(
        request: Request[None, None, State],
    ) -> dict[str, object]:
        await _maybe_verify(request)
        return _RESOURCE_TYPES

    @get("/scim/v2/Schemas")
    async def schemas(
        request: Request[None, None, State],
    ) -> dict[str, object]:
        await _maybe_verify(request)
        return _SCHEMAS

    return Router(path="/", route_handlers=[service_provider_config, resource_types, schemas])
