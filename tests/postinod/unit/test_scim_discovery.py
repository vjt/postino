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
