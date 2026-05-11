"""HybridProvider unit tests.

Uses an in-memory SQLite engine with a stripped `mailbox` table since
HybridProvider only touches the `password` and `modified` columns.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine, select
from sqlalchemy.engine import Engine

from postino_core.enums import PasswordScheme
from postino_core.errors import NotFoundError
from postino_core.providers.base import SENTINEL_NOAUTH
from postino_core.providers.hybrid import HybridProvider


@pytest.fixture
def metadata() -> MetaData:
    md = MetaData()
    Table(
        "mailbox",
        md,
        Column("username", String(255), primary_key=True),
        Column("password", String(255), nullable=False),
        Column("modified", DateTime, nullable=False),
        Column("active", Integer, nullable=False, default=1),
    )
    return md


@pytest.fixture
def engine(metadata: MetaData) -> Engine:
    eng = create_engine("sqlite://")
    metadata.create_all(eng)
    return eng


def _clock() -> datetime:
    return datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC)


def _insert(engine: Engine, metadata: MetaData, *, username: str, password: str) -> None:
    mb = metadata.tables["mailbox"]
    with engine.begin() as conn:
        conn.execute(
            mb.insert().values(username=username, password=password, modified=_clock(), active=1)
        )


def _pwd(engine: Engine, metadata: MetaData, username: str) -> str:
    mb = metadata.tables["mailbox"]
    with engine.connect() as conn:
        return str(
            conn.execute(select(mb.c.password).where(mb.c.username == username)).scalar_one()
        )


def test_capabilities_all_true() -> None:
    p = HybridProvider(metadata=MetaData(), clock=_clock)
    assert p.supports_password_change() is True
    assert p.supports_local_provisioning() is True
    assert p.supports_release_to_noauth() is True


def test_create_identity_without_password_keeps_sentinel(
    engine: Engine, metadata: MetaData
) -> None:
    _insert(engine, metadata, username="u@x.io", password=SENTINEL_NOAUTH)
    p = HybridProvider(metadata=metadata, clock=_clock)
    with engine.begin() as conn:
        p.create_identity(conn, "u@x.io", name="U", password=None, scheme=None)
    assert _pwd(engine, metadata, "u@x.io") == SENTINEL_NOAUTH


def test_create_identity_with_password_writes_hash(engine: Engine, metadata: MetaData) -> None:
    _insert(engine, metadata, username="u@x.io", password=SENTINEL_NOAUTH)
    p = HybridProvider(metadata=metadata, clock=_clock)
    with engine.begin() as conn:
        p.create_identity(
            conn, "u@x.io", name="U", password=SecretStr("hunter2"), scheme=PasswordScheme.BCRYPT
        )
    pwd = _pwd(engine, metadata, "u@x.io")
    assert pwd.startswith("{BLF-CRYPT}")
    assert pwd != SENTINEL_NOAUTH


def test_set_password_over_sentinel_emits_claim_warning(
    engine: Engine, metadata: MetaData, caplog: pytest.LogCaptureFixture
) -> None:
    _insert(engine, metadata, username="u@x.io", password=SENTINEL_NOAUTH)
    p = HybridProvider(metadata=metadata, clock=_clock)
    with (
        caplog.at_level(logging.WARNING, logger="postino_core.providers.hybrid"),
        engine.begin() as conn,
    ):
        p.set_password(conn, "u@x.io", SecretStr("hunter2"), PasswordScheme.BCRYPT)
    assert any("claimed into SQL auth" in r.message for r in caplog.records)
    assert _pwd(engine, metadata, "u@x.io").startswith("{BLF-CRYPT}")


def test_set_password_over_hash_no_warning(
    engine: Engine, metadata: MetaData, caplog: pytest.LogCaptureFixture
) -> None:
    _insert(engine, metadata, username="u@x.io", password="{BLF-CRYPT}$2b$12$x...")
    p = HybridProvider(metadata=metadata, clock=_clock)
    with (
        caplog.at_level(logging.WARNING, logger="postino_core.providers.hybrid"),
        engine.begin() as conn,
    ):
        p.set_password(conn, "u@x.io", SecretStr("hunter2"), PasswordScheme.BCRYPT)
    assert not any("claimed" in r.message for r in caplog.records)


def test_release_over_hash_emits_warning(
    engine: Engine, metadata: MetaData, caplog: pytest.LogCaptureFixture
) -> None:
    _insert(engine, metadata, username="u@x.io", password="{BLF-CRYPT}$2b$12$x...")
    p = HybridProvider(metadata=metadata, clock=_clock)
    with (
        caplog.at_level(logging.WARNING, logger="postino_core.providers.hybrid"),
        engine.begin() as conn,
    ):
        p.release_identity(conn, "u@x.io")
    assert any("released to IdP" in r.message for r in caplog.records)
    assert _pwd(engine, metadata, "u@x.io") == SENTINEL_NOAUTH


def test_release_over_sentinel_idempotent(
    engine: Engine, metadata: MetaData, caplog: pytest.LogCaptureFixture
) -> None:
    _insert(engine, metadata, username="u@x.io", password=SENTINEL_NOAUTH)
    p = HybridProvider(metadata=metadata, clock=_clock)
    with (
        caplog.at_level(logging.WARNING, logger="postino_core.providers.hybrid"),
        engine.begin() as conn,
    ):
        p.release_identity(conn, "u@x.io")
    assert not any("released" in r.message for r in caplog.records)
    assert _pwd(engine, metadata, "u@x.io") == SENTINEL_NOAUTH


def test_set_password_missing_row_raises(engine: Engine, metadata: MetaData) -> None:
    p = HybridProvider(metadata=metadata, clock=_clock)
    with engine.begin() as conn, pytest.raises(NotFoundError):
        p.set_password(conn, "missing@x.io", SecretStr("x"), PasswordScheme.BCRYPT)


def test_release_missing_row_raises(engine: Engine, metadata: MetaData) -> None:
    p = HybridProvider(metadata=metadata, clock=_clock)
    with engine.begin() as conn, pytest.raises(NotFoundError):
        p.release_identity(conn, "missing@x.io")


def test_delete_identity_is_noop(engine: Engine, metadata: MetaData) -> None:
    _insert(engine, metadata, username="u@x.io", password="{BLF-CRYPT}xxx")
    p = HybridProvider(metadata=metadata, clock=_clock)
    with engine.begin() as conn:
        p.delete_identity(conn, "u@x.io")
    # row + password column untouched
    assert _pwd(engine, metadata, "u@x.io") == "{BLF-CRYPT}xxx"
