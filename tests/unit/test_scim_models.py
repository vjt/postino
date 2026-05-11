"""ScimUser password field is write-only (accepted on input, never serialized)."""

from __future__ import annotations

from pydantic import SecretStr

from postinod.scim.models import USER_SCHEMA, ScimName, ScimUser


def test_password_accepted_on_construct() -> None:
    user = ScimUser(
        schemas=[USER_SCHEMA],
        userName="u@x.io",
        name=ScimName(formatted="U"),
        password=SecretStr("hunter2"),
    )
    assert user.password is not None
    assert user.password.get_secret_value() == "hunter2"


def test_password_excluded_from_dump() -> None:
    user = ScimUser(
        schemas=[USER_SCHEMA],
        userName="u@x.io",
        name=ScimName(formatted="U"),
        password=SecretStr("hunter2"),
    )
    dumped = user.model_dump(by_alias=True, exclude_none=True)
    assert "password" not in dumped
    serialized = user.model_dump_json(by_alias=True, exclude_none=True)
    assert "password" not in serialized
    assert "hunter2" not in serialized


def test_password_repr_redacted() -> None:
    user = ScimUser(
        schemas=[USER_SCHEMA],
        userName="u@x.io",
        name=ScimName(formatted="U"),
        password=SecretStr("hunter2"),
    )
    assert "hunter2" not in repr(user)
    assert "hunter2" not in str(user)
