"""SCIM 2.0 resource models — RFC 7644 round-trip."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from postinod.scim.models import (
    PatchOpRequest,
    ScimAlias,
    ScimError,
    ScimListResponse,
    ScimUser,
)

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"


def test_scim_user_round_trip() -> None:
    raw = {
        "schemas": [USER_SCHEMA],
        "userName": "alice@example.org",
        "name": {"formatted": "Alice Rossi", "givenName": "Alice", "familyName": "Rossi"},
        "emails": [{"value": "alice@example.org", "primary": True}],
        "active": True,
    }
    u = ScimUser.model_validate(raw)
    assert u.user_name == "alice@example.org"
    assert u.name.formatted == "Alice Rossi"
    assert u.active is True


def test_scim_user_missing_username_rejected() -> None:
    with pytest.raises(ValidationError):
        ScimUser.model_validate({"schemas": [USER_SCHEMA], "active": True})


def test_scim_user_wrong_schema_rejected() -> None:
    with pytest.raises(ValidationError):
        ScimUser.model_validate(
            {
                "schemas": ["urn:wrong"],
                "userName": "x@y.org",
                "name": {"formatted": "X Y"},
                "active": True,
            }
        )


def test_patch_op_replace_active() -> None:
    raw = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    }
    p = PatchOpRequest.model_validate(raw)
    assert len(p.operations) == 1
    assert p.operations[0].op == "replace"
    assert p.operations[0].path == "active"
    assert p.operations[0].value is False


def test_patch_op_path_filter_rejected_at_app_layer() -> None:
    # Filter expressions parse OK at the model layer; the router rejects them
    # with 400 invalidPath (tested in scim users router task).
    raw = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [
            {"op": "replace", "path": "emails[primary eq true].value", "value": "x@y.org"}
        ],
    }
    p = PatchOpRequest.model_validate(raw)
    path = p.operations[0].path
    assert path is not None and "[" in path  # router treats this as unsupported


def test_scim_error_serialization() -> None:
    e = ScimError(status="404", detail="not found")
    j = e.model_dump(by_alias=True, exclude_none=True)
    assert j["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert j["status"] == "404"
    assert j["detail"] == "not found"


def test_scim_list_response_round_trip() -> None:
    lr = ScimListResponse.model_validate({"totalResults": 2, "Resources": [{"a": 1}, {"b": 2}]})
    j = lr.model_dump(by_alias=True)
    assert j["totalResults"] == 2
    assert j["Resources"] == [{"a": 1}, {"b": 2}]


def test_scim_alias_round_trip() -> None:
    raw = {
        "schemas": ["urn:postino:params:scim:schemas:core:2.0:Alias"],
        "address": "team@example.org",
        "goto": "alice@example.org,bob@example.org",
    }
    a = ScimAlias.model_validate(raw)
    assert a.address == "team@example.org"
    assert a.goto == "alice@example.org,bob@example.org"
