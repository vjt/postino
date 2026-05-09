import pytest
from pydantic import SecretStr
from sqlalchemy import MetaData, select
from sqlalchemy.engine import Connection, Engine

from postino_core.enums import PasswordScheme
from postino_core.errors import NotFoundError
from postino_core.password import verify_password
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
            password="{NOAUTH}",
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
        prov = LocalProvider(metadata=md)
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
        prov = LocalProvider(metadata=md)
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
        prov = LocalProvider(metadata=md)
        with pytest.raises(NotFoundError):
            prov.set_password(conn, "ghost@example.com", SecretStr("x"), PasswordScheme.BCRYPT)


def test_supports_password_change_true() -> None:
    prov = LocalProvider(metadata=MetaData())
    assert prov.supports_password_change() is True
