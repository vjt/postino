"""SCIM 2.0 e2e: real container stack, JWT-bearer requests via httpx.

We use httpx rather than scim2-cli to keep e2e dependency-light; the
sequence is what scim2-cli would issue.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.e2e

BASE = "http://localhost:18443/scim/v2"


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/scim+json"}


def test_full_user_lifecycle(stack: None, bearer_token: str) -> None:
    # CREATE
    user = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "e2e1@example.org",
        "name": {"formatted": "E2E One"},
        "active": True,
    }
    r = httpx.post(f"{BASE}/Users", json=user, headers=_h(bearer_token))
    assert r.status_code == 201, r.text

    # GET
    r = httpx.get(f"{BASE}/Users/e2e1@example.org", headers=_h(bearer_token))
    assert r.status_code == 200

    # PATCH (disable)
    patch = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    }
    r = httpx.patch(f"{BASE}/Users/e2e1@example.org", json=patch, headers=_h(bearer_token))
    assert r.status_code == 200

    # DELETE (soft)
    r = httpx.delete(f"{BASE}/Users/e2e1@example.org", headers=_h(bearer_token))
    assert r.status_code == 204


def test_alias_lifecycle(stack: None, bearer_token: str) -> None:
    alias = {
        "schemas": ["urn:postino:params:scim:schemas:core:2.0:Alias"],
        "address": "team-e2e@example.org",
        "goto": "e2e1@example.org",
    }
    r = httpx.post(f"{BASE}/Aliases", json=alias, headers=_h(bearer_token))
    assert r.status_code == 201

    r = httpx.delete(f"{BASE}/Aliases/team-e2e@example.org", headers=_h(bearer_token))
    assert r.status_code == 204


def test_resource_types_advertises_alias(stack: None, bearer_token: str) -> None:
    r = httpx.get(f"{BASE}/ResourceTypes", headers=_h(bearer_token))
    assert r.status_code == 200
    ids = {res["id"] for res in r.json()["Resources"]}
    assert {"User", "Alias"} <= ids


def test_unauthenticated_returns_401(stack: None) -> None:
    # POST /Users without Authorization header — rejected before routing
    r = httpx.post(
        f"{BASE}/Users",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "nobody@example.org",
        },
        headers={"Content-Type": "application/scim+json"},
    )
    assert r.status_code == 401
