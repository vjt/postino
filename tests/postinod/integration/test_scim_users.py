"""SCIM Users router integration: real DB, stub JWKS, signed JWTs."""

from __future__ import annotations

import base64
import collections.abc
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from litestar import Litestar
from litestar.testing import AsyncTestClient
from sqlalchemy import select

from .conftest import PreparedTestDB

pytestmark = pytest.mark.integration

ISSUER = "https://idp.test"
AUDIENCE = "postinod"
KID = "test-kid"


@pytest.fixture(scope="module")
def keypair() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def auth_header(keypair: RSAPrivateKey) -> dict[str, str]:
    pem = keypair.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    token: str = jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "scim-client",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        pem,  # type: ignore[arg-type]  # WHY: cryptography returns bytes from private_bytes; pyjwt accepts bytes | str but is typed as str
        algorithm="RS256",
        headers={"kid": KID},
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def client(
    prepared_test_db: PreparedTestDB,
    keypair: RSAPrivateKey,
) -> collections.abc.AsyncGenerator[AsyncTestClient[Litestar], None]:
    from postinod.app import build_app_for_test

    def _b64(i: int) -> str:
        b = i.to_bytes((i.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).decode().rstrip("=")

    pub_numbers = keypair.public_key().public_numbers()
    jwk: dict[str, object] = {
        "kty": "RSA",
        "kid": KID,
        "use": "sig",
        "alg": "RS256",
        "n": _b64(pub_numbers.n),
        "e": _b64(pub_numbers.e),
    }

    app = build_app_for_test(
        db_engine=prepared_test_db.engine,
        metadata=prepared_test_db.metadata,
        hmac_secret=b"unused",
        scim_issuer=ISSUER,
        scim_audience=AUDIENCE,
        jwks_stub_keys=[jwk],
    )
    async with AsyncTestClient(app=app) as c:
        yield c


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
