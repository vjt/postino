"""SCIM POST /Users password handling — unit-level wiring."""

from __future__ import annotations

import pytest

from postino_core.enums import PasswordScheme
from postinod.scim import users
from postinod.scim.models import ScimUser


@pytest.mark.cli
def test_post_with_password_routes_to_mailbox_create_with_secret() -> None:
    """SCIM POST handler unpacks password into MailboxCreate."""
    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "u@x.io",
        "name": {"formatted": "U"},
        "password": "hunter2",
    }
    parsed = ScimUser.model_validate(body)
    mc = users._make_mailbox_create(parsed, default_quota_bytes=1024 * 1024)  # pyright: ignore[reportPrivateUsage]  # WHY: module-private helper exercised directly to assert wiring
    assert mc.password is not None
    assert mc.password.get_secret_value() == "hunter2"
    assert mc.scheme is PasswordScheme.BCRYPT


@pytest.mark.cli
def test_post_without_password_yields_none() -> None:
    parsed = ScimUser.model_validate(
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "u@x.io",
            "name": {"formatted": "U"},
        }
    )
    mc = users._make_mailbox_create(parsed, default_quota_bytes=1024 * 1024)  # pyright: ignore[reportPrivateUsage]  # WHY: module-private helper exercised directly to assert wiring
    assert mc.password is None
    assert mc.scheme is None
