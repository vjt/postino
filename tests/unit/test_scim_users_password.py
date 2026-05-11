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


@pytest.mark.cli
def test_patch_password_dispatch_table() -> None:
    """Pure-function dispatcher: maps PatchOp → ("set"|"release", value)."""
    from postinod.scim import users
    from postinod.scim.models import PatchOp

    set_op = PatchOp(op="replace", path="password", value="hunter2")
    assert users._patch_password_intent(set_op) == ("set", "hunter2")  # pyright: ignore[reportPrivateUsage]  # WHY: module-private helper exercised directly to assert dialect normalisation

    null_op = PatchOp(op="replace", path="password", value=None)
    assert users._patch_password_intent(null_op) == ("release", None)  # pyright: ignore[reportPrivateUsage]  # WHY: module-private helper exercised directly to assert dialect normalisation

    empty_op = PatchOp(op="replace", path="password", value="")
    assert users._patch_password_intent(empty_op) == ("release", None)  # pyright: ignore[reportPrivateUsage]  # WHY: module-private helper exercised directly to assert dialect normalisation

    remove_op = PatchOp(op="remove", path="password", value=None)
    assert users._patch_password_intent(remove_op) == ("release", None)  # pyright: ignore[reportPrivateUsage]  # WHY: module-private helper exercised directly to assert dialect normalisation
