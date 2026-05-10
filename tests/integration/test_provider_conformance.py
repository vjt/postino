"""Conformance test: every IdentityProvider satisfies the Protocol.

Parametrised across the two shipped providers (Local + NoAuth). Each
test exercises a contract the bundle and MailboxService rely on; if a
new provider ships and forgets one, this file fails.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import SecretStr
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

from postino_core.enums import PasswordScheme
from postino_core.providers import IdentityProvider, LocalProvider, NoAuthProvider

pytestmark = pytest.mark.integration


def _build_local(md: MetaData) -> IdentityProvider:
    return LocalProvider(metadata=md)


def _build_noauth(md: MetaData) -> IdentityProvider:
    return NoAuthProvider()


@pytest.fixture(
    params=[
        ("local", _build_local),
        ("noauth", _build_noauth),
    ],
    ids=lambda p: p[0],
)
def provider(request: pytest.FixtureRequest, db: Engine) -> tuple[IdentityProvider, MetaData]:
    _, build = request.param  # type: ignore[misc]  # WHY: pytest's request.param is typed as Any.
    md = MetaData()
    md.reflect(bind=db)
    builder: Callable[[MetaData], IdentityProvider] = build
    return builder(md), md


def test_supports_predicates_are_bool(
    provider: tuple[IdentityProvider, MetaData],
) -> None:
    p, _ = provider
    assert isinstance(p.supports_password_change(), bool)
    assert isinstance(p.supports_local_provisioning(), bool)


def test_delete_identity_is_callable(
    provider: tuple[IdentityProvider, MetaData],
    db: Engine,
) -> None:
    """delete_identity must accept a missing username without raising."""
    p, _ = provider
    with db.begin() as conn:
        p.delete_identity(conn, "ghost@example.com")


def test_set_password_either_succeeds_or_raises_typed_error(
    provider: tuple[IdentityProvider, MetaData],
    db: Engine,
) -> None:
    """LocalProvider raises NotFoundError; NoAuthProvider raises ConfigError.

    Both are MailctlError subclasses — the CLI maps them to clean exits."""
    from postino_core.errors import MailctlError

    p, _ = provider
    with db.begin() as conn:
        try:
            p.set_password(
                conn,
                "ghost@example.com",
                SecretStr("x"),
                PasswordScheme.BCRYPT,
            )
        except MailctlError:
            pass
        except Exception as e:
            pytest.fail(f"non-MailctlError raised: {type(e).__name__}: {e}")
