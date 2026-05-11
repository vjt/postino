"""NoAuthProvider — credential ops are no-ops or refusals."""

from __future__ import annotations

import pytest
from pydantic import SecretStr
from sqlalchemy import MetaData, select
from sqlalchemy.engine import Connection, Engine

from postino_core.enums import PasswordScheme
from postino_core.errors import ConfigError
from postino_core.providers import SENTINEL_NOAUTH
from postino_core.providers.noauth import NoAuthProvider

pytestmark = pytest.mark.integration


def _seed_mailbox(conn: Connection, md: MetaData, username: str) -> None:
    domain = md.tables["domain"]
    mailbox = md.tables["mailbox"]
    conn.execute(
        domain.insert().values(
            domain="example.com",
            description="",
            aliases=0,
            mailboxes=0,
            maxquota=0,
            quota=0,
            transport="virtual",
            backupmx=0,
            active=1,
        )
    )
    conn.execute(
        mailbox.insert().values(
            username=username,
            password=SENTINEL_NOAUTH,
            name="",
            maildir="example.com/foo/",
            quota=0,
            local_part="foo",
            domain="example.com",
            active=1,
        )
    )


def test_create_identity_is_noop_keeps_sentinel(db: Engine) -> None:
    """NoAuthProvider.create_identity must not touch mailbox.password."""
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        _seed_mailbox(conn, md, "foo@example.com")
        prov = NoAuthProvider()
        prov.create_identity(
            conn,
            "foo@example.com",
            name="Foo",
            password=None,
            scheme=None,
        )
        row = conn.execute(
            select(md.tables["mailbox"].c.password).where(
                md.tables["mailbox"].c.username == "foo@example.com"
            )
        ).scalar_one()
    assert row == SENTINEL_NOAUTH


def test_create_identity_rejects_password_argument(db: Engine) -> None:
    """A caller passing a password under NoAuth is a configuration bug —
    the secret would be silently discarded (the sentinel stays in
    mailbox.password). Reject loudly so callers gate on
    ``supports_local_provisioning()`` or hand ``None``."""
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        _seed_mailbox(conn, md, "foo@example.com")
        prov = NoAuthProvider()
        with pytest.raises(ConfigError):
            prov.create_identity(
                conn,
                "foo@example.com",
                name="Foo",
                password=SecretStr("would-be-leaked"),
                scheme=PasswordScheme.BCRYPT,
            )
        row = conn.execute(
            select(md.tables["mailbox"].c.password).where(
                md.tables["mailbox"].c.username == "foo@example.com"
            )
        ).scalar_one()
    assert row == SENTINEL_NOAUTH


def test_set_password_raises_config_error() -> None:
    prov = NoAuthProvider()
    with pytest.raises(ConfigError):
        prov.set_password(
            conn=None,  # type: ignore[arg-type]  # WHY: ConfigError must be raised before any conn use.
            username="foo@example.com",
            password=SecretStr("x"),
            scheme=PasswordScheme.BCRYPT,
        )


def test_delete_identity_is_noop() -> None:
    NoAuthProvider().delete_identity(
        conn=None,  # type: ignore[arg-type]  # WHY: no-op never touches the connection.
        username="foo@example.com",
    )


def test_supports_predicates_both_false() -> None:
    prov = NoAuthProvider()
    assert prov.supports_password_change() is False
    assert prov.supports_local_provisioning() is False
