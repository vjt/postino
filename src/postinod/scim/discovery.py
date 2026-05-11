"""SCIM 2.0 discovery endpoints (RFC 7644 §4).

Three GET endpoints surface server capabilities:
* /scim/v2/ServiceProviderConfig — supported features (patch, bulk, etc.)
* /scim/v2/ResourceTypes — list of resource types we expose
* /scim/v2/Schemas — schema definitions for those resource types

The Schemas payload is derived from pydantic introspection over
`ScimUser`, `ScimAlias`, `ScimDomain` so adding a field to any of those
models updates the published schema automatically.

If `jwt_verifier` is None the endpoints are unauthenticated (suitable
for unit tests and IdP probes that hit discovery without auth). When
wired in production (Task 15) the JWT verifier is supplied and
inline-verifies the bearer token per RFC 7644 §3.5.
"""

from __future__ import annotations

import types
from datetime import datetime
from typing import Annotated, Union, get_args, get_origin

import jwt
from litestar import Request, Router, get
from litestar.datastructures import State
from litestar.exceptions import HTTPException
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from postinod.auth.jwt_guard import JwtVerifier
from postinod.scim.models import (
    ALIAS_SCHEMA,
    DOMAIN_SCHEMA,
    USER_SCHEMA,
    ScimAlias,
    ScimDomain,
    ScimUser,
)

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
    "totalResults": 3,
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
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
            "id": "Domain",
            "name": "Domain",
            "endpoint": "/Domains",
            "schema": "urn:postino:params:scim:schemas:core:2.0:Domain",
        },
    ],
}

# SCIM common attributes (RFC 7643 §3.1) are not republished per resource.
_COMMON_ATTRS = {"schemas", "id", "external_id", "meta"}


def _strip_annotated(tp: object) -> object:
    if get_origin(tp) is Annotated:
        return get_args(tp)[0]
    return tp


def _unwrap_optional(tp: object) -> object:
    tp = _strip_annotated(tp)
    origin = get_origin(tp)
    if origin in (types.UnionType, Union):
        non_none = [a for a in get_args(tp) if a is not type(None)]
        if len(non_none) == 1:
            return _strip_annotated(non_none[0])
    return tp


def _scim_type(annotation: object) -> tuple[str, type[BaseModel] | None]:
    """Map a python type to a (SCIM type, sub-model) pair.

    sub-model is non-None when the SCIM type is `complex` and the caller
    must recurse to emit `subAttributes`.
    """
    ann = _unwrap_optional(annotation)
    if isinstance(ann, type):
        if issubclass(ann, bool):
            return "boolean", None
        if issubclass(ann, BaseModel):
            return "complex", ann
        if issubclass(ann, int):
            return "integer", None
        if issubclass(ann, datetime):
            return "dateTime", None
        if issubclass(ann, str):
            return "string", None
    # WHY: pydantic EmailStr / Annotated str resolve to bare str via _strip_annotated
    # above; this fallback covers anything else and keeps the schema payload valid.
    return "string", None


def _attribute_from_field(name: str, info: FieldInfo) -> dict[str, object]:
    annotation = _strip_annotated(info.annotation)
    multi_valued = False
    if get_origin(_unwrap_optional(annotation)) is list:
        (inner,) = get_args(_unwrap_optional(annotation))
        multi_valued = True
        annotation = inner
    type_name, sub_model = _scim_type(annotation)
    out: dict[str, object] = {
        "name": info.alias or name,
        "type": type_name,
    }
    if multi_valued:
        out["multiValued"] = True
    if info.is_required():
        out["required"] = True
    if sub_model is not None:
        out["subAttributes"] = [
            _attribute_from_field(sub_name, sub_info)
            for sub_name, sub_info in sub_model.model_fields.items()
        ]
    return out


def _introspect_schema(
    model_cls: type[BaseModel],
    schema_id: str,
    name: str,
    *,
    unique_fields: set[str],
) -> dict[str, object]:
    attrs: list[dict[str, object]] = []
    for field_name, info in model_cls.model_fields.items():
        if field_name in _COMMON_ATTRS:
            continue
        attr = _attribute_from_field(field_name, info)
        if field_name in unique_fields:
            attr["uniqueness"] = "server"
        attrs.append(attr)
    return {
        "id": schema_id,
        "name": name,
        "attributes": attrs,
    }


def _build_schemas() -> dict[str, object]:
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": 3,
        "Resources": [
            _introspect_schema(ScimUser, USER_SCHEMA, "User", unique_fields={"user_name"}),
            _introspect_schema(ScimAlias, ALIAS_SCHEMA, "Alias", unique_fields={"address"}),
            _introspect_schema(ScimDomain, DOMAIN_SCHEMA, "Domain", unique_fields={"domain"}),
        ],
    }


_SCHEMAS: dict[str, object] = _build_schemas()


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
