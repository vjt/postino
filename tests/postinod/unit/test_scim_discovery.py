"""SCIM discovery endpoints — minimal RFC 7644 §4 stubs."""

from __future__ import annotations

import collections.abc

import pytest
from litestar import Litestar
from litestar.testing import AsyncTestClient

from postinod.scim.discovery import build_discovery_router

SPC_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"


@pytest.fixture
async def client() -> collections.abc.AsyncGenerator[AsyncTestClient[Litestar], None]:
    """Discovery endpoints unauthenticated for the unit test surface.

    Production wiring (Task 15) places them behind the JWT verifier per
    RFC 7644 §3.5.
    """
    app = Litestar(route_handlers=[build_discovery_router(jwt_verifier=None)])
    async with AsyncTestClient(app=app) as c:
        yield c


async def test_service_provider_config(client: AsyncTestClient[Litestar]) -> None:
    r = await client.get("/scim/v2/ServiceProviderConfig")
    assert r.status_code == 200
    j = r.json()
    assert SPC_SCHEMA in j["schemas"]
    assert j["patch"]["supported"] is True
    assert j["bulk"]["supported"] is False


async def test_resource_types(client: AsyncTestClient[Litestar]) -> None:
    r = await client.get("/scim/v2/ResourceTypes")
    assert r.status_code == 200
    j = r.json()
    ids = {item["id"] for item in j["Resources"]}
    assert "User" in ids
    assert "Alias" in ids


async def test_schemas(client: AsyncTestClient[Litestar]) -> None:
    r = await client.get("/scim/v2/Schemas")
    assert r.status_code == 200
    j = r.json()
    schema_ids = {s["id"] for s in j["Resources"]}
    assert "urn:ietf:params:scim:schemas:core:2.0:User" in schema_ids
    assert "urn:postino:params:scim:schemas:core:2.0:Alias" in schema_ids
    assert "urn:postino:params:scim:schemas:core:2.0:Domain" in schema_ids


async def test_schemas_introspection_shape(client: AsyncTestClient[Litestar]) -> None:
    """Pydantic introspection must emit the right SCIM shape per resource."""
    r = await client.get("/scim/v2/Schemas")
    j = r.json()
    by_id = {s["id"]: s for s in j["Resources"]}

    user = by_id["urn:ietf:params:scim:schemas:core:2.0:User"]
    user_attrs = {a["name"]: a for a in user["attributes"]}
    # userName: required string, server-uniqueness
    assert user_attrs["userName"]["type"] == "string"
    assert user_attrs["userName"]["required"] is True
    assert user_attrs["userName"]["uniqueness"] == "server"
    # name: complex with sub-attributes
    assert user_attrs["name"]["type"] == "complex"
    sub = {a["name"] for a in user_attrs["name"]["subAttributes"]}
    assert sub == {"formatted", "givenName", "familyName"}
    # emails: multiValued complex
    assert user_attrs["emails"]["type"] == "complex"
    assert user_attrs["emails"]["multiValued"] is True
    # active: boolean
    assert user_attrs["active"]["type"] == "boolean"
    # common attributes (schemas/id/meta/externalId) must NOT leak into per-resource attrs
    assert "schemas" not in user_attrs
    assert "id" not in user_attrs
    assert "meta" not in user_attrs

    alias = by_id["urn:postino:params:scim:schemas:core:2.0:Alias"]
    alias_attrs = {a["name"]: a for a in alias["attributes"]}
    assert alias_attrs["address"]["uniqueness"] == "server"
    assert alias_attrs["goto"]["required"] is True

    domain = by_id["urn:postino:params:scim:schemas:core:2.0:Domain"]
    dom_attrs = {a["name"]: a for a in domain["attributes"]}
    assert dom_attrs["domain"]["uniqueness"] == "server"
    assert dom_attrs["maxAliases"]["type"] == "integer"
    assert dom_attrs["backupmx"]["type"] == "boolean"
    assert dom_attrs["transport"]["type"] == "string"
