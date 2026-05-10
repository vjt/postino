"""SCIM list-endpoint integration tests for /Users, /Aliases, /Domains.

Covers:
* envelope shape (schemas, totalResults, itemsPerPage, startIndex, Resources)
* pagination (startIndex, count)
* filter `<attr> eq "<value>"` (eq-only grammar; everything else 400)
* auth gate (missing bearer → 401)
* empty result (totalResults=0)
"""

from __future__ import annotations

import pytest
from litestar import Litestar
from litestar.testing import AsyncTestClient
from sqlalchemy import insert

from .conftest import PreparedTestDB

pytestmark = pytest.mark.integration

LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"


async def _seed_users(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    usernames: list[str],
) -> None:
    for u in usernames:
        body = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": u,
            "name": {"formatted": u.split("@")[0].title()},
            "active": True,
        }
        r = await client.post("/scim/v2/Users", json=body, headers=auth_header)
        assert r.status_code == 201, r.text


async def _seed_aliases(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    pairs: list[tuple[str, str]],
) -> None:
    for address, goto in pairs:
        body = {
            "schemas": ["urn:postino:params:scim:schemas:core:2.0:Alias"],
            "address": address,
            "goto": goto,
        }
        r = await client.post("/scim/v2/Aliases", json=body, headers=auth_header)
        assert r.status_code == 201, r.text


# -------- Users --------


async def test_list_users_empty(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
) -> None:
    r = await client.get("/scim/v2/Users", headers=auth_header)
    assert r.status_code == 200
    j = r.json()
    assert LIST_SCHEMA in j["schemas"]
    assert j["totalResults"] == 0
    assert j["itemsPerPage"] == 0
    assert j["startIndex"] == 1
    assert j["Resources"] == []


async def test_list_users_returns_seeded_rows(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
) -> None:
    await _seed_users(client, auth_header, ["a@example.org", "b@example.org", "c@example.org"])
    r = await client.get("/scim/v2/Users", headers=auth_header)
    assert r.status_code == 200
    j = r.json()
    assert j["totalResults"] == 3
    assert j["itemsPerPage"] == 3
    ids = sorted(res["id"] for res in j["Resources"])
    assert ids == ["a@example.org", "b@example.org", "c@example.org"]


async def test_list_users_pagination(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
) -> None:
    await _seed_users(
        client,
        auth_header,
        [f"u{i}@example.org" for i in range(5)],
    )
    r = await client.get(
        "/scim/v2/Users?startIndex=2&count=2",
        headers=auth_header,
    )
    assert r.status_code == 200
    j = r.json()
    assert j["totalResults"] == 5
    assert j["startIndex"] == 2
    assert j["itemsPerPage"] == 2
    assert len(j["Resources"]) == 2


async def test_list_users_filter_username_eq(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
) -> None:
    await _seed_users(client, auth_header, ["x@example.org", "y@example.org"])
    r = await client.get(
        '/scim/v2/Users?filter=userName eq "x@example.org"',
        headers=auth_header,
    )
    assert r.status_code == 200
    j = r.json()
    assert j["totalResults"] == 1
    assert j["Resources"][0]["userName"] == "x@example.org"


async def test_list_users_filter_domain_eq_returns_zero_for_unknown(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
) -> None:
    await _seed_users(client, auth_header, ["a@example.org"])
    r = await client.get(
        '/scim/v2/Users?filter=domain eq "nope.example.org"',
        headers=auth_header,
    )
    assert r.status_code == 200
    assert r.json()["totalResults"] == 0


async def test_list_users_invalid_filter_returns_400(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
) -> None:
    r = await client.get(
        '/scim/v2/Users?filter=userName co "x"',
        headers=auth_header,
    )
    assert r.status_code == 400
    assert r.json()["scimType"] == "invalidFilter"


async def test_list_users_requires_auth(
    client: AsyncTestClient[Litestar],
) -> None:
    r = await client.get("/scim/v2/Users")
    assert r.status_code == 401


# -------- Aliases --------


async def test_list_aliases_returns_seeded_rows(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
) -> None:
    await _seed_users(client, auth_header, ["target@example.org"])
    await _seed_aliases(
        client,
        auth_header,
        [
            ("alias1@example.org", "target@example.org"),
            ("alias2@example.org", "target@example.org"),
        ],
    )
    r = await client.get("/scim/v2/Aliases", headers=auth_header)
    assert r.status_code == 200
    j = r.json()
    assert j["totalResults"] == 2
    addrs = sorted(res["address"] for res in j["Resources"])
    assert addrs == ["alias1@example.org", "alias2@example.org"]


async def test_list_aliases_filter_address_eq(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
) -> None:
    await _seed_users(client, auth_header, ["target@example.org"])
    await _seed_aliases(
        client,
        auth_header,
        [
            ("a@example.org", "target@example.org"),
            ("b@example.org", "target@example.org"),
        ],
    )
    r = await client.get(
        '/scim/v2/Aliases?filter=address eq "a@example.org"',
        headers=auth_header,
    )
    assert r.status_code == 200
    j = r.json()
    assert j["totalResults"] == 1
    assert j["Resources"][0]["address"] == "a@example.org"


async def test_list_aliases_requires_auth(
    client: AsyncTestClient[Litestar],
) -> None:
    r = await client.get("/scim/v2/Aliases")
    assert r.status_code == 401


# -------- Domains --------


async def test_list_domains_returns_seeded_domain(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
) -> None:
    r = await client.get("/scim/v2/Domains", headers=auth_header)
    assert r.status_code == 200
    j = r.json()
    assert j["totalResults"] == 1
    res = j["Resources"][0]
    assert res["domain"] == "example.org"
    assert res["maxMailboxes"] == 100
    assert res["transport"] == "virtual"
    assert res["active"] is True


async def test_list_domains_filter_domain_eq(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
) -> None:
    domain = prepared_test_db.metadata.tables["domain"]
    with prepared_test_db.engine.begin() as conn:
        conn.execute(
            insert(domain).values(
                domain="other.test",
                description="second",
                aliases=10,
                mailboxes=10,
                maxquota=0,
                quota=0,
                transport="virtual",
                backupmx=0,
                active=1,
            )
        )

    r = await client.get(
        '/scim/v2/Domains?filter=domain eq "other.test"',
        headers=auth_header,
    )
    assert r.status_code == 200
    j = r.json()
    assert j["totalResults"] == 1
    assert j["Resources"][0]["domain"] == "other.test"


async def test_get_domain_returns_single(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
    prepared_test_db: PreparedTestDB,
) -> None:
    r = await client.get("/scim/v2/Domains/example.org", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "example.org"
    assert body["domain"] == "example.org"


async def test_get_domain_unknown_returns_404(
    client: AsyncTestClient[Litestar],
    auth_header: dict[str, str],
) -> None:
    r = await client.get("/scim/v2/Domains/nope.example.org", headers=auth_header)
    assert r.status_code == 404


async def test_list_domains_requires_auth(
    client: AsyncTestClient[Litestar],
) -> None:
    r = await client.get("/scim/v2/Domains")
    assert r.status_code == 401
