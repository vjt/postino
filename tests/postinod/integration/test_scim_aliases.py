"""SCIM /Aliases router (postino custom resource) integration tests."""

from __future__ import annotations

import pytest
from litestar import Litestar
from litestar.testing import AsyncTestClient
from sqlalchemy import select

from .conftest import PreparedTestDB

pytestmark = pytest.mark.integration


async def test_post_creates_alias(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
) -> None:
    body = {
        "schemas": ["urn:postino:params:scim:schemas:core:2.0:Alias"],
        "address": "team@example.org",
        "goto": "alice@example.org,bob@example.org",
    }
    r = await client.post("/scim/v2/Aliases", json=body, headers=auth_header)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["id"] == "team@example.org"

    alias = prepared_test_db.metadata.tables["alias"]
    with prepared_test_db.engine.connect() as conn:
        row = conn.execute(select(alias).where(alias.c.address == "team@example.org")).fetchone()
    assert row is not None
    assert row.goto == "alice@example.org,bob@example.org"


async def test_delete_removes_alias(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
) -> None:
    body = {
        "schemas": ["urn:postino:params:scim:schemas:core:2.0:Alias"],
        "address": "list@example.org",
        "goto": "alice@example.org",
    }
    await client.post("/scim/v2/Aliases", json=body, headers=auth_header)
    r = await client.delete("/scim/v2/Aliases/list@example.org", headers=auth_header)
    assert r.status_code == 204

    alias = prepared_test_db.metadata.tables["alias"]
    with prepared_test_db.engine.connect() as conn:
        row = conn.execute(select(alias).where(alias.c.address == "list@example.org")).fetchone()
    assert row is None


async def test_capacity_exceeded_returns_400(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
) -> None:
    """Domain alias cap exhaustion surfaces as 400 tooMany.

    PostfixAdmin semantics: aliases=0 means unlimited; aliases=N>0 caps at N.
    We create one alias then cap the domain at 1, so the second POST exhausts
    the quota.
    """
    # Create one alias to consume the quota slot we are about to set.
    seed = {
        "schemas": ["urn:postino:params:scim:schemas:core:2.0:Alias"],
        "address": "first@example.org",
        "goto": "alice@example.org",
    }
    r0 = await client.post("/scim/v2/Aliases", json=seed, headers=auth_header)
    assert r0.status_code == 201, r0.text

    # Now cap the domain at 1 (the slot we just filled).
    domain = prepared_test_db.metadata.tables["domain"]
    with prepared_test_db.engine.begin() as conn:
        conn.execute(domain.update().where(domain.c.domain == "example.org").values(aliases=1))

    body = {
        "schemas": ["urn:postino:params:scim:schemas:core:2.0:Alias"],
        "address": "denied@example.org",
        "goto": "alice@example.org",
    }
    r = await client.post("/scim/v2/Aliases", json=body, headers=auth_header)
    assert r.status_code == 400
    assert r.json()["scimType"] == "tooMany"


async def test_unauthenticated_alias_post_rejected(
    client: AsyncTestClient[Litestar],
) -> None:
    body = {
        "schemas": ["urn:postino:params:scim:schemas:core:2.0:Alias"],
        "address": "anon@example.org",
        "goto": "alice@example.org",
    }
    r = await client.post("/scim/v2/Aliases", json=body)
    assert r.status_code == 401


async def test_get_returns_alias(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
) -> None:
    body = {
        "schemas": ["urn:postino:params:scim:schemas:core:2.0:Alias"],
        "address": "marketing@example.org",
        "goto": "alice@example.org",
    }
    await client.post("/scim/v2/Aliases", json=body, headers=auth_header)
    r = await client.get("/scim/v2/Aliases/marketing@example.org", headers=auth_header)
    assert r.status_code == 200
    assert r.json()["address"] == "marketing@example.org"
    assert r.json()["goto"] == "alice@example.org"


async def test_get_nonexistent_alias_returns_404(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
) -> None:
    r = await client.get("/scim/v2/Aliases/missing@example.org", headers=auth_header)
    assert r.status_code == 404


async def test_patch_active_false(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
) -> None:
    """PATCH op=replace path=active value=false disables an existing alias."""
    body = {
        "schemas": ["urn:postino:params:scim:schemas:core:2.0:Alias"],
        "address": "patchdown@example.org",
        "goto": "alice@example.org",
    }
    r0 = await client.post("/scim/v2/Aliases", json=body, headers=auth_header)
    assert r0.status_code == 201, r0.text

    patch = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    }
    r = await client.patch(
        "/scim/v2/Aliases/patchdown@example.org", json=patch, headers=auth_header
    )
    assert r.status_code == 200, r.text
    assert r.json()["active"] is False

    alias = prepared_test_db.metadata.tables["alias"]
    with prepared_test_db.engine.connect() as conn:
        row = conn.execute(
            select(alias).where(alias.c.address == "patchdown@example.org")
        ).fetchone()
    assert row is not None
    assert row.active == 0


async def test_patch_active_true_round_trip(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
) -> None:
    """Disable then re-enable an alias via PATCH; final state is active."""
    body = {
        "schemas": ["urn:postino:params:scim:schemas:core:2.0:Alias"],
        "address": "roundtrip@example.org",
        "goto": "alice@example.org",
    }
    r0 = await client.post("/scim/v2/Aliases", json=body, headers=auth_header)
    assert r0.status_code == 201, r0.text

    disable = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    }
    r1 = await client.patch(
        "/scim/v2/Aliases/roundtrip@example.org", json=disable, headers=auth_header
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["active"] is False

    enable = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": True}],
    }
    r2 = await client.patch(
        "/scim/v2/Aliases/roundtrip@example.org", json=enable, headers=auth_header
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["active"] is True

    alias = prepared_test_db.metadata.tables["alias"]
    with prepared_test_db.engine.connect() as conn:
        row = conn.execute(
            select(alias).where(alias.c.address == "roundtrip@example.org")
        ).fetchone()
    assert row is not None
    assert row.active == 1


async def test_patch_unsupported_path_returns_400(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
) -> None:
    """PATCH with an unsupported path (e.g. goto) → 400 invalidPath."""
    body = {
        "schemas": ["urn:postino:params:scim:schemas:core:2.0:Alias"],
        "address": "badpath@example.org",
        "goto": "alice@example.org",
    }
    await client.post("/scim/v2/Aliases", json=body, headers=auth_header)
    patch = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "goto", "value": "bob@example.org"}],
    }
    r = await client.patch("/scim/v2/Aliases/badpath@example.org", json=patch, headers=auth_header)
    assert r.status_code == 400
    assert r.json()["scimType"] == "invalidPath"


async def test_patch_active_missing_alias_returns_404(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
) -> None:
    """PATCH on a non-existent alias → 404."""
    patch = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    }
    r = await client.patch("/scim/v2/Aliases/nobody@example.org", json=patch, headers=auth_header)
    assert r.status_code == 404


async def test_patch_requires_bearer(client: AsyncTestClient[Litestar]) -> None:
    """PATCH with no Authorization header → 401."""
    patch = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    }
    r = await client.patch("/scim/v2/Aliases/anyone@example.org", json=patch)
    assert r.status_code == 401


async def test_alias_envelope_meta_and_content_type(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
) -> None:
    body = {
        "schemas": ["urn:postino:params:scim:schemas:core:2.0:Alias"],
        "address": "envmeta@example.org",
        "goto": "alice@example.org",
    }
    r = await client.post("/scim/v2/Aliases", json=body, headers=auth_header)
    assert r.status_code == 201, r.text
    assert r.headers["content-type"].startswith("application/scim+json")
    j = r.json()
    assert j["schemas"] == ["urn:postino:params:scim:schemas:core:2.0:Alias"]
    meta = j["meta"]
    assert meta["resourceType"] == "Alias"
    assert meta["location"] == "/scim/v2/Aliases/envmeta@example.org"
    assert "created" in meta and "lastModified" in meta

    g = await client.get("/scim/v2/Aliases/envmeta@example.org", headers=auth_header)
    assert g.status_code == 200
    assert g.headers["content-type"].startswith("application/scim+json")
    assert g.json()["meta"]["resourceType"] == "Alias"
