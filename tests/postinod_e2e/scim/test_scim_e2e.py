"""SCIM 2.0 e2e: real container stack, JWT-bearer requests via httpx.

Requests use httpx for simplicity; envelopes are round-tripped through
`scim2-models` so the server output is validated against the SCIM 2.0
schema definitions (RFC 7643 / 7644), not just shape-asserted.
"""

from __future__ import annotations

import httpx
import pytest
from scim2_models import (
    ListResponse,
    ResourceType,
    Schema,
    ServiceProviderConfig,
    User,
)

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
    assert r.headers["content-type"].startswith("application/scim+json")
    body = r.json()
    # Spec-grade validation: drops if scim2-models rejects our envelope.
    User.model_validate(body)  # type: ignore[type-arg]  # WHY: scim2_models.User is generic over extensions; we don't subclass
    assert body["userName"] == "e2e1@example.org"
    assert body["meta"]["resourceType"] == "User"

    # GET
    r = httpx.get(f"{BASE}/Users/e2e1@example.org", headers=_h(bearer_token))
    assert r.status_code == 200
    User.model_validate(r.json())  # type: ignore[type-arg]  # WHY: as above

    # PATCH (disable)
    patch = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    }
    r = httpx.patch(f"{BASE}/Users/e2e1@example.org", json=patch, headers=_h(bearer_token))
    assert r.status_code == 200
    User.model_validate(r.json())  # type: ignore[type-arg]  # WHY: as above
    assert r.json()["active"] is False

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
    assert r.headers["content-type"].startswith("application/scim+json")
    body = r.json()
    assert body["meta"]["resourceType"] == "Alias"
    assert body["meta"]["location"] == "/scim/v2/Aliases/team-e2e@example.org"

    r = httpx.delete(f"{BASE}/Aliases/team-e2e@example.org", headers=_h(bearer_token))
    assert r.status_code == 204


def test_resource_types_advertises_alias(stack: None, bearer_token: str) -> None:
    r = httpx.get(f"{BASE}/ResourceTypes", headers=_h(bearer_token))
    assert r.status_code == 200
    envelope = ListResponse[ResourceType].model_validate(r.json())
    assert envelope.resources is not None
    ids = {rt.id for rt in envelope.resources}
    assert {"User", "Alias", "Domain"} <= ids


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


def test_service_provider_config_validates(stack: None, bearer_token: str) -> None:
    r = httpx.get(f"{BASE}/ServiceProviderConfig", headers=_h(bearer_token))
    assert r.status_code == 200
    spc = ServiceProviderConfig.model_validate(r.json())
    assert spc.patch is not None and spc.patch.supported is True
    assert spc.bulk is not None and spc.bulk.supported is False
    assert spc.filter is not None and spc.filter.supported is True


def test_schemas_validates(stack: None, bearer_token: str) -> None:
    r = httpx.get(f"{BASE}/Schemas", headers=_h(bearer_token))
    assert r.status_code == 200
    envelope = ListResponse[Schema].model_validate(r.json())
    assert envelope.resources is not None
    schema_ids = {s.id for s in envelope.resources}
    assert "urn:ietf:params:scim:schemas:core:2.0:User" in schema_ids
    assert "urn:postino:params:scim:schemas:core:2.0:Alias" in schema_ids
    assert "urn:postino:params:scim:schemas:core:2.0:Domain" in schema_ids


def test_users_pagination(stack: None, bearer_token: str) -> None:
    # Seed two distinct users so pagination has something to slice.
    for i in (1, 2):
        body = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": f"page{i}@example.org",
            "name": {"formatted": f"Page {i}"},
            "active": True,
        }
        httpx.post(f"{BASE}/Users", json=body, headers=_h(bearer_token))

    r = httpx.get(f"{BASE}/Users?startIndex=1&count=1", headers=_h(bearer_token))
    assert r.status_code == 200
    envelope = r.json()
    assert envelope["itemsPerPage"] == 1
    assert envelope["totalResults"] >= 2
    assert len(envelope["Resources"]) == 1


def test_users_filter_eq_happy_path(stack: None, bearer_token: str) -> None:
    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "filterhit@example.org",
        "name": {"formatted": "Filter Hit"},
        "active": True,
    }
    httpx.post(f"{BASE}/Users", json=body, headers=_h(bearer_token))

    r = httpx.get(
        f"{BASE}/Users",
        params={"filter": 'userName eq "filterhit@example.org"'},
        headers=_h(bearer_token),
    )
    assert r.status_code == 200
    envelope = r.json()
    assert envelope["totalResults"] == 1
    assert envelope["Resources"][0]["userName"] == "filterhit@example.org"


def test_users_invalid_filter_returns_400(stack: None, bearer_token: str) -> None:
    r = httpx.get(
        f"{BASE}/Users",
        params={"filter": "userName co garbage)"},
        headers=_h(bearer_token),
    )
    assert r.status_code == 400
    assert r.json()["scimType"] == "invalidFilter"


def test_domain_get_validates(stack: None, bearer_token: str) -> None:
    r = httpx.get(f"{BASE}/Domains/example.org", headers=_h(bearer_token))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/scim+json")
    body = r.json()
    assert body["schemas"] == ["urn:postino:params:scim:schemas:core:2.0:Domain"]
    assert body["meta"]["resourceType"] == "Domain"
    assert body["meta"]["location"] == "/scim/v2/Domains/example.org"
