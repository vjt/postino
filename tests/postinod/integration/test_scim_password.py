"""SCIM /Users password lifecycle — integration test.

Boots the test app factory with ``identity_backend=HYBRID`` against the
integration DB and exercises POST + PATCH password through the full
HTTP layer (JWT verification → SCIM router → MailboxService →
HybridProvider → real MariaDB).

Covers:

* POST with password → ``{BLF-CRYPT}`` hash written, response omits ``password``.
* POST without password → ``{NOAUTH}`` sentinel written.
* PATCH replace + string value → set_password (sentinel → BCRYPT).
* PATCH replace + ``null`` value → release_identity (Azure dialect).
* PATCH remove → release_identity (Okta dialect).
* PATCH replace + string under NOAUTH backend → 403 ``mutability``.

Marked ``integration`` per pyproject.toml: requires ``POSTINO_TEST_DB_URL``.
"""

from __future__ import annotations

import pytest
from litestar import Litestar
from litestar.testing import AsyncTestClient
from sqlalchemy import select

from postino_core.providers.base import SENTINEL_NOAUTH

from .conftest import PreparedTestDB, scim_headers

pytestmark = pytest.mark.integration


async def test_post_user_with_password_writes_bcrypt(
    hybrid_client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
) -> None:
    """POST /Users with password → ``mailbox.password`` starts with ``{BLF-CRYPT}``."""
    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "alice@example.org",
        "name": {"formatted": "Alice A"},
        "password": "hunter2",
        "active": True,
    }
    r = await hybrid_client.post("/scim/v2/Users", json=body, headers=scim_headers(auth_header))
    assert r.status_code == 201, r.text
    # SCIM RFC 7643 §7 — password is write-only; must not leak in the response.
    assert "password" not in r.text

    mailbox = prepared_test_db.metadata.tables["mailbox"]
    with prepared_test_db.engine.connect() as conn:
        row = conn.execute(
            select(mailbox).where(mailbox.c.username == "alice@example.org")
        ).fetchone()
    assert row is not None
    assert str(row.password).startswith("{BLF-CRYPT}")

    log = prepared_test_db.metadata.tables["log"]
    with prepared_test_db.engine.connect() as conn:
        rows = (
            conn.execute(select(log).where(log.c.action == "postinod.user.create")).mappings().all()
        )
    assert any('"surface":"scim"' in row["data"] for row in rows)
    assert any('"email":"alice@example.org"' in row["data"] for row in rows)


async def test_post_user_without_password_writes_sentinel(
    hybrid_client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
) -> None:
    """POST /Users without password under HYBRID → ``{NOAUTH}`` sentinel persisted."""
    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "bob@example.org",
        "name": {"formatted": "Bob B"},
        "active": True,
    }
    r = await hybrid_client.post("/scim/v2/Users", json=body, headers=scim_headers(auth_header))
    assert r.status_code == 201, r.text

    mailbox = prepared_test_db.metadata.tables["mailbox"]
    with prepared_test_db.engine.connect() as conn:
        row = conn.execute(
            select(mailbox).where(mailbox.c.username == "bob@example.org")
        ).fetchone()
    assert row is not None
    assert str(row.password) == SENTINEL_NOAUTH


async def test_patch_password_replace_with_string_writes_bcrypt(
    hybrid_client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
    fresh_sentinel_user: str,
) -> None:
    """PATCH replace path=password value=<str> on a sentinel row → BCRYPT hash."""
    body = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "password", "value": "hunter2"}],
    }
    r = await hybrid_client.patch(
        f"/scim/v2/Users/{fresh_sentinel_user}",
        json=body,
        headers=scim_headers(auth_header),
    )
    assert r.status_code == 200, r.text

    mailbox = prepared_test_db.metadata.tables["mailbox"]
    with prepared_test_db.engine.connect() as conn:
        row = conn.execute(
            select(mailbox).where(mailbox.c.username == fresh_sentinel_user)
        ).fetchone()
    assert row is not None
    assert str(row.password).startswith("{BLF-CRYPT}")

    log = prepared_test_db.metadata.tables["log"]
    with prepared_test_db.engine.connect() as conn:
        rows = (
            conn.execute(select(log).where(log.c.action == "postinod.user.passwd")).mappings().all()
        )
    assert any('"surface":"scim"' in row["data"] for row in rows)
    assert any(f'"email":"{fresh_sentinel_user}"' in row["data"] for row in rows)


async def test_patch_password_replace_null_releases(
    hybrid_client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
    fresh_bcrypt_user: str,
) -> None:
    """Azure-style PATCH replace path=password value=null → release_identity."""
    body = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "password", "value": None}],
    }
    r = await hybrid_client.patch(
        f"/scim/v2/Users/{fresh_bcrypt_user}",
        json=body,
        headers=scim_headers(auth_header),
    )
    assert r.status_code == 200, r.text

    mailbox = prepared_test_db.metadata.tables["mailbox"]
    with prepared_test_db.engine.connect() as conn:
        row = conn.execute(
            select(mailbox).where(mailbox.c.username == fresh_bcrypt_user)
        ).fetchone()
    assert row is not None
    assert str(row.password) == SENTINEL_NOAUTH

    log = prepared_test_db.metadata.tables["log"]
    with prepared_test_db.engine.connect() as conn:
        rows = (
            conn.execute(select(log).where(log.c.action == "postinod.user.release"))
            .mappings()
            .all()
        )
    assert any('"surface":"scim"' in row["data"] for row in rows)
    assert any(f'"email":"{fresh_bcrypt_user}"' in row["data"] for row in rows)


async def test_patch_password_remove_releases(
    hybrid_client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
    fresh_bcrypt_user: str,
) -> None:
    """Okta-style PATCH remove path=password → release_identity."""
    body = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "remove", "path": "password"}],
    }
    r = await hybrid_client.patch(
        f"/scim/v2/Users/{fresh_bcrypt_user}",
        json=body,
        headers=scim_headers(auth_header),
    )
    assert r.status_code == 200, r.text

    mailbox = prepared_test_db.metadata.tables["mailbox"]
    with prepared_test_db.engine.connect() as conn:
        row = conn.execute(
            select(mailbox).where(mailbox.c.username == fresh_bcrypt_user)
        ).fetchone()
    assert row is not None
    assert str(row.password) == SENTINEL_NOAUTH


async def test_patch_password_on_noauth_backend_returns_403(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    fresh_sentinel_user: str,
) -> None:
    """SCIM PATCH password under identity_backend=NOAUTH → 403 ``mutability``.

    ``fresh_sentinel_user`` is created via the hybrid client (POST without
    password seeds a ``{NOAUTH}`` sentinel row), then the patch goes
    through the NOAUTH-backed ``client``. Both ``hybrid_client`` and
    ``client`` resolve to the same function-scoped ``prepared_test_db``
    engine within a single test — the shared engine is what lets one
    client seed a row that the other client then PATCHes. Module-scoping
    any of the db fixtures (``db`` in tests/conftest.py or
    ``prepared_test_db`` here) would silently break this test by giving
    each client a different schema.
    """
    body = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "password", "value": "hunter2"}],
    }
    r = await client.patch(
        f"/scim/v2/Users/{fresh_sentinel_user}",
        json=body,
        headers=scim_headers(auth_header),
    )
    assert r.status_code == 403, r.text
    assert r.json()["scimType"] == "mutability"
