"""Smoke tests for tests/fakes/identity.FakeIdentityProvider.

A bitrot guard: if the IdentityProvider Protocol changes shape, the
fake will fail these tests before downstream consumers (mailbox-service
unit tests in PR-A5+ work) hit harder-to-debug breakages."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from postino_core.enums import PasswordScheme
from postino_core.providers import IdentityProvider
from tests.fakes.identity import FakeIdentityProvider


def test_fake_satisfies_protocol() -> None:
    fake = FakeIdentityProvider()
    p: IdentityProvider = fake  # structural type check
    assert p.supports_password_change() is True
    assert p.supports_local_provisioning() is True


def test_fake_records_calls() -> None:
    fake = FakeIdentityProvider()
    fake.create_identity(
        conn=None,  # type: ignore[arg-type]  # WHY: fake never touches the connection.
        username="foo@example.com",
        name="Foo",
        password=SecretStr("p"),
        scheme=PasswordScheme.BCRYPT,
    )
    fake.set_password(
        conn=None,  # type: ignore[arg-type]  # WHY: fake never touches the connection.
        username="foo@example.com",
        password=SecretStr("p"),
        scheme=PasswordScheme.BCRYPT,
    )
    fake.delete_identity(
        conn=None,  # type: ignore[arg-type]  # WHY: fake never touches the connection.
        username="foo@example.com",
    )
    assert [c.op for c in fake.calls] == ["create", "set_password", "delete"]


def test_fake_fail_on_create_raises() -> None:
    fake = FakeIdentityProvider(fail_on="create")
    with pytest.raises(RuntimeError):
        fake.create_identity(
            conn=None,  # type: ignore[arg-type]  # WHY: fake never touches the connection.
            username="foo@example.com",
            name="Foo",
            password=SecretStr("p"),
            scheme=PasswordScheme.BCRYPT,
        )


def test_fake_predicate_overrides() -> None:
    fake = FakeIdentityProvider(
        supports_password_change_value=False,
        supports_local_provisioning_value=False,
    )
    assert fake.supports_password_change() is False
    assert fake.supports_local_provisioning() is False
