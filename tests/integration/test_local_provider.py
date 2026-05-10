from datetime import datetime

import pytest
from pydantic import SecretStr
from sqlalchemy import MetaData, select
from sqlalchemy.engine import Connection, Engine

from postino_core.enums import PasswordScheme
from postino_core.errors import ConfigError, NotFoundError
from postino_core.password import verify_password
from postino_core.providers import SENTINEL_NOAUTH
from postino_core.providers.local import LocalProvider

pytestmark = pytest.mark.integration


def _seed_mailbox(conn: Connection, md: MetaData, username: str) -> None:
    mailbox = md.tables["mailbox"]
    domain = md.tables["domain"]
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
            name="Foo",
            maildir="example.com/foo/",
            quota=0,
            local_part="foo",
            domain="example.com",
            active=1,
        )
    )


def test_local_create_identity_writes_password(db: Engine) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        _seed_mailbox(conn, md, "foo@example.com")
        prov = LocalProvider(metadata=md, clock=lambda: datetime(2026, 5, 9, 12, 0, 0))
        prov.create_identity(
            conn,
            "foo@example.com",
            name="Foo",
            password=SecretStr("hunter2"),
            scheme=PasswordScheme.BCRYPT,
        )
        row = conn.execute(
            select(md.tables["mailbox"].c.password).where(
                md.tables["mailbox"].c.username == "foo@example.com"
            )
        ).scalar_one()
    assert row.startswith("{BLF-CRYPT}")
    assert verify_password(SecretStr("hunter2"), row) is True


def test_local_set_password_overrides(db: Engine) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        _seed_mailbox(conn, md, "foo@example.com")
        prov = LocalProvider(metadata=md, clock=lambda: datetime(2026, 5, 9, 12, 0, 0))
        prov.create_identity(
            conn,
            "foo@example.com",
            name="",
            password=SecretStr("a"),
            scheme=PasswordScheme.BCRYPT,
        )
        prov.set_password(conn, "foo@example.com", SecretStr("b"), PasswordScheme.BCRYPT)
        row = conn.execute(
            select(md.tables["mailbox"].c.password).where(
                md.tables["mailbox"].c.username == "foo@example.com"
            )
        ).scalar_one()
    assert verify_password(SecretStr("b"), row) is True


def test_local_set_password_missing_raises(db: Engine) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        prov = LocalProvider(metadata=md, clock=lambda: datetime(2026, 5, 9, 12, 0, 0))
        with pytest.raises(NotFoundError):
            prov.set_password(conn, "ghost@example.com", SecretStr("x"), PasswordScheme.BCRYPT)


def test_supports_password_change_true() -> None:
    prov = LocalProvider(metadata=MetaData(), clock=lambda: datetime(2026, 5, 9, 12, 0, 0))
    assert prov.supports_password_change() is True


def test_supports_local_provisioning_true() -> None:
    prov = LocalProvider(metadata=MetaData(), clock=lambda: datetime(2026, 5, 9, 12, 0, 0))
    assert prov.supports_local_provisioning() is True


def test_set_password_bumps_modified(db: Engine) -> None:
    """`set_password` must update mailbox.modified — otherwise the audit
    log says the row is untouched but the credential changed under it.
    Clock is injected so the test can assert an exact value."""
    md = MetaData()
    md.reflect(bind=db)
    mailbox = md.tables["mailbox"]

    seed_at = datetime(2025, 1, 1, 0, 0, 0)
    bump_at = datetime(2026, 5, 9, 12, 0, 0)

    with db.begin() as conn:
        domain = md.tables["domain"]
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
                created=seed_at,
                modified=seed_at,
            )
        )
        conn.execute(
            mailbox.insert().values(
                username="foo@example.com",
                password=SENTINEL_NOAUTH,
                name="Foo",
                maildir="example.com/foo/",
                quota=0,
                local_part="foo",
                domain="example.com",
                active=1,
                created=seed_at,
                modified=seed_at,
            )
        )
        prov = LocalProvider(metadata=md, clock=lambda: bump_at)
        prov.set_password(conn, "foo@example.com", SecretStr("hunter2"), PasswordScheme.BCRYPT)

    with db.begin() as conn:
        modified = conn.execute(
            select(mailbox.c.modified).where(mailbox.c.username == "foo@example.com")
        ).scalar_one()
    assert modified == bump_at


def test_create_identity_without_password_raises(db: Engine) -> None:
    """LocalProvider refuses to provision a mailbox without password+scheme."""
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        _seed_mailbox(conn, md, "foo@example.com")
        prov = LocalProvider(metadata=md, clock=lambda: datetime(2026, 5, 9, 12, 0, 0))
        with pytest.raises(ConfigError):
            prov.create_identity(
                conn,
                "foo@example.com",
                name="",
                password=None,
                scheme=PasswordScheme.BCRYPT,
            )
        with pytest.raises(ConfigError):
            prov.create_identity(
                conn,
                "foo@example.com",
                name="",
                password=SecretStr("p"),
                scheme=None,
            )
