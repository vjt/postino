"""SCIM Users router integration: real DB, stub JWKS, signed JWTs."""

from __future__ import annotations

import pytest
from litestar import Litestar
from litestar.testing import AsyncTestClient
from sqlalchemy import select

from .conftest import PreparedTestDB

pytestmark = pytest.mark.integration


async def test_post_creates_user(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
) -> None:
    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "eve@example.org",
        "name": {"formatted": "Eve E", "givenName": "Eve", "familyName": "E"},
        "emails": [{"value": "eve@example.org", "primary": True}],
        "active": True,
    }
    r = await client.post("/scim/v2/Users", json=body, headers=auth_header)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["id"] == "eve@example.org"
    assert r.headers["Location"].endswith("/scim/v2/Users/eve@example.org")


async def test_get_returns_user(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
) -> None:
    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "frank@example.org",
        "name": {"formatted": "Frank F"},
        "active": True,
    }
    await client.post("/scim/v2/Users", json=body, headers=auth_header)
    r = await client.get("/scim/v2/Users/frank@example.org", headers=auth_header)
    assert r.status_code == 200
    assert r.json()["userName"] == "frank@example.org"


async def test_duplicate_returns_409(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
) -> None:
    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "gina@example.org",
        "name": {"formatted": "Gina G"},
        "active": True,
    }
    await client.post("/scim/v2/Users", json=body, headers=auth_header)
    r = await client.post("/scim/v2/Users", json=body, headers=auth_header)
    assert r.status_code == 409
    assert r.json()["scimType"] == "uniqueness"


async def test_patch_active_disables_user(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
) -> None:
    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "hank@example.org",
        "name": {"formatted": "Hank H"},
        "active": True,
    }
    await client.post("/scim/v2/Users", json=body, headers=auth_header)
    patch = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    }
    r = await client.patch("/scim/v2/Users/hank@example.org", json=patch, headers=auth_header)
    assert r.status_code == 200

    mailbox = prepared_test_db.metadata.tables["mailbox"]
    with prepared_test_db.engine.connect() as conn:
        row = conn.execute(
            select(mailbox).where(mailbox.c.username == "hank@example.org")
        ).fetchone()
    assert row is not None
    assert row.active == 0


async def test_delete_soft_deletes(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
) -> None:
    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "ivy@example.org",
        "name": {"formatted": "Ivy I"},
        "active": True,
    }
    await client.post("/scim/v2/Users", json=body, headers=auth_header)
    r = await client.delete("/scim/v2/Users/ivy@example.org", headers=auth_header)
    assert r.status_code == 204

    mailbox = prepared_test_db.metadata.tables["mailbox"]
    with prepared_test_db.engine.connect() as conn:
        row = conn.execute(
            select(mailbox).where(mailbox.c.username == "ivy@example.org")
        ).fetchone()
    assert row is not None  # still on disk
    assert row.active == 0  # but disabled


async def test_unauthenticated_rejected(client: AsyncTestClient[Litestar]) -> None:
    r = await client.get("/scim/v2/Users/anyone@example.org")
    assert r.status_code == 401


async def test_unsupported_patch_path_returns_400(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
) -> None:
    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "jane@example.org",
        "name": {"formatted": "Jane J"},
        "active": True,
    }
    await client.post("/scim/v2/Users", json=body, headers=auth_header)
    patch = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "emails[primary eq true].value", "value": "x@y"}],
    }
    r = await client.patch("/scim/v2/Users/jane@example.org", json=patch, headers=auth_header)
    assert r.status_code == 400
    assert r.json()["scimType"] == "invalidPath"


async def test_get_nonexistent_user_returns_404(
    client: AsyncTestClient[Litestar], auth_header: dict[str, str]
) -> None:
    r = await client.get("/scim/v2/Users/missing@example.org", headers=auth_header)
    assert r.status_code == 404


async def test_patch_name_formatted_updates_display_name(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
) -> None:
    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "kira@example.org",
        "name": {"formatted": "Kira K"},
        "active": True,
    }
    await client.post("/scim/v2/Users", json=body, headers=auth_header)
    patch = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "name.formatted", "value": "Kira Krause"}],
    }
    r = await client.patch("/scim/v2/Users/kira@example.org", json=patch, headers=auth_header)
    assert r.status_code == 200
    mailbox = prepared_test_db.metadata.tables["mailbox"]
    with prepared_test_db.engine.connect() as conn:
        row = conn.execute(
            select(mailbox).where(mailbox.c.username == "kira@example.org")
        ).fetchone()
    assert row is not None
    assert row.name == "Kira Krause"


async def test_post_writes_audit_row(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
) -> None:
    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "leo@example.org",
        "name": {"formatted": "Leo L"},
        "active": True,
    }
    r = await client.post("/scim/v2/Users", json=body, headers=auth_header)
    assert r.status_code == 201
    log = prepared_test_db.metadata.tables["log"]
    with prepared_test_db.engine.connect() as conn:
        rows = (
            conn.execute(select(log).where(log.c.action == "postinod.user.create")).mappings().all()
        )
    assert any('"surface":"scim"' in row["data"] for row in rows)
    assert any('"email":"leo@example.org"' in row["data"] for row in rows)
