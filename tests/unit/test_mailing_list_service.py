"""Unit tests for MailingListService._validate_no_collision.

Uses in-memory SQLite + a minimal fake MlmmjAdapter so these run without
any filesystem or DB integration requirement.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Column, MetaData, String, Table, create_engine
from sqlalchemy.dialects.sqlite import BOOLEAN, SMALLINT
from sqlalchemy.engine import Engine

from postino_core.errors import AlreadyExistsError
from postino_core.repos.routes import RoutesRepository
from postino_core.services.mailing_list import MailingListService


def _fake_metadata() -> MetaData:
    """Minimal in-memory schema: mailbox, alias, routes — enough for collision checks."""
    md = MetaData()
    Table(
        "mailbox",
        md,
        Column("username", String(255), primary_key=True),
        Column("password", String(255), nullable=False, default=""),
        Column("name", String(255), nullable=False, default=""),
        Column("maildir", String(255), nullable=False, default=""),
        Column("quota", String(255), nullable=False, default="0"),
        Column("local_part", String(255), nullable=False, default=""),
        Column("domain", String(255), nullable=False, default=""),
        Column("active", SMALLINT, nullable=False, default=1),
        Column("created", String(32), nullable=False, default=""),
        Column("modified", String(32), nullable=False, default=""),
    )
    Table(
        "alias",
        md,
        Column("address", String(255), primary_key=True),
        Column("goto", String(255), nullable=False, default=""),
        Column("domain", String(255), nullable=False, default=""),
        Column("active", SMALLINT, nullable=False, default=1),
        Column("created", String(32), nullable=False, default=""),
        Column("modified", String(32), nullable=False, default=""),
    )
    Table(
        "routes",
        md,
        Column("pattern", String(255), primary_key=True),
        Column("transport", String(64), nullable=False),
        Column("domain", String(255), nullable=False),
        Column("list_address", String(255), nullable=True),
        Column("priority", SMALLINT, nullable=False, default=50),
        Column("active", BOOLEAN, nullable=False, default=True),
    )
    # audit table required by AuditWriter / DefaultAuditWriter
    Table(
        "log",
        md,
        Column("timestamp", String(32), nullable=False, default=""),
        Column("username", String(255), nullable=False, default=""),
        Column("domain", String(255), nullable=False, default=""),
        Column("action", String(255), nullable=False, default=""),
        Column("data", String(255), nullable=False, default=""),
        Column("id", String(64), primary_key=True, default=""),
    )
    return md


def _fake_adapter(*, exists: bool = False) -> MagicMock:
    adapter = MagicMock()
    adapter.exists.return_value = exists
    return adapter


def _service(md: MetaData, adapter: MagicMock) -> tuple[MailingListService, Engine]:
    engine = create_engine("sqlite:///:memory:")
    md.create_all(engine)
    svc = MailingListService(
        engine=engine,
        metadata=md,
        adapter=adapter,  # type: ignore[arg-type]  # WHY: MagicMock satisfies the MlmmjAdapter protocol for these unit tests
        routes=RoutesRepository(engine=engine, metadata=md),
        clock=lambda: datetime(2026, 1, 1),
    )
    return svc, engine


# ---------------------------------------------------------------------------
# Existing checks — sanity-assert they still work after the extension
# ---------------------------------------------------------------------------


def test_validate_no_collision_passes_on_empty_tables() -> None:
    md = _fake_metadata()
    svc, engine = _service(md, _fake_adapter(exists=False))
    with engine.begin() as conn:
        svc._validate_no_collision(conn, "team@lists.example.org")  # pyright: ignore[reportPrivateUsage]  # WHY: testing private method directly to isolate collision logic


def test_validate_no_collision_rejects_existing_mailbox() -> None:
    md = _fake_metadata()
    svc, engine = _service(md, _fake_adapter(exists=False))
    with engine.begin() as conn:
        conn.execute(
            md.tables["mailbox"]
            .insert()
            .values(
                username="team@lists.example.org",
                password="{NOAUTH}",
                name="Team",
                maildir="lists.example.org/team/",
                quota="0",
                local_part="team",
                domain="lists.example.org",
                active=1,
                created="2026-01-01",
                modified="2026-01-01",
            )
        )
    with engine.begin() as conn, pytest.raises(AlreadyExistsError, match="mailbox row"):
        svc._validate_no_collision(conn, "team@lists.example.org")  # pyright: ignore[reportPrivateUsage]  # WHY: testing private method directly to isolate collision logic


def test_validate_no_collision_rejects_existing_alias_exact() -> None:
    md = _fake_metadata()
    svc, engine = _service(md, _fake_adapter(exists=False))
    with engine.begin() as conn:
        conn.execute(
            md.tables["alias"]
            .insert()
            .values(
                address="team@lists.example.org",
                goto="someone@example.org",
                domain="lists.example.org",
                active=1,
                created="2026-01-01",
                modified="2026-01-01",
            )
        )
    with engine.begin() as conn, pytest.raises(AlreadyExistsError, match="alias row"):
        svc._validate_no_collision(conn, "team@lists.example.org")  # pyright: ignore[reportPrivateUsage]  # WHY: testing private method directly to isolate collision logic


def test_validate_no_collision_rejects_adapter_spool_exists() -> None:
    md = _fake_metadata()
    svc, engine = _service(md, _fake_adapter(exists=True))
    with engine.begin() as conn, pytest.raises(AlreadyExistsError, match="already exists"):
        svc._validate_no_collision(conn, "team@lists.example.org")  # pyright: ignore[reportPrivateUsage]  # WHY: testing private method directly to isolate collision logic


# ---------------------------------------------------------------------------
# NEW: routes + -owner alias collision checks (Task 13)
# ---------------------------------------------------------------------------


def test_validate_no_collision_rejects_existing_routes_row() -> None:
    """A routes row with list_address == address must raise AlreadyExistsError."""
    md = _fake_metadata()
    svc, engine = _service(md, _fake_adapter(exists=False))
    with engine.begin() as conn:
        conn.execute(
            md.tables["routes"]
            .insert()
            .values(
                pattern=r"^team(\+.+)?@lists\.example\.org$",
                transport="mlmmj-receive:",
                domain="lists.example.org",
                list_address="team@lists.example.org",
                priority=50,
                active=True,
            )
        )
    with engine.begin() as conn, pytest.raises(AlreadyExistsError, match="routes row"):
        svc._validate_no_collision(conn, "team@lists.example.org")  # pyright: ignore[reportPrivateUsage]  # WHY: testing private method directly to isolate collision logic


def test_validate_no_collision_rejects_existing_owner_alias() -> None:
    """An alias row for <localpart>-owner@<domain> must raise AlreadyExistsError."""
    md = _fake_metadata()
    svc, engine = _service(md, _fake_adapter(exists=False))
    with engine.begin() as conn:
        conn.execute(
            md.tables["alias"]
            .insert()
            .values(
                address="team-owner@lists.example.org",
                goto="alice@example.org",
                domain="lists.example.org",
                active=1,
                created="2026-01-01",
                modified="2026-01-01",
            )
        )
    with engine.begin() as conn, pytest.raises(AlreadyExistsError, match="alias row"):
        svc._validate_no_collision(conn, "team@lists.example.org")  # pyright: ignore[reportPrivateUsage]  # WHY: testing private method directly to isolate collision logic


# ---------------------------------------------------------------------------
# Task 15: _write_owner_alias + _delete_owner_alias helpers
# ---------------------------------------------------------------------------


def test_write_owner_alias_inserts_alias_row() -> None:
    """_write_owner_alias inserts alias row with goto=owners joined by comma."""
    md = _fake_metadata()
    svc, engine = _service(md, _fake_adapter())
    with engine.begin() as conn:
        svc._write_owner_alias(  # pyright: ignore[reportPrivateUsage]  # WHY: testing private helper directly
            conn,
            "team@lists.example.org",
            ["alice@example.org", "bob@example.org"],
        )
    alias = md.tables["alias"]
    from sqlalchemy import select as sa_select

    with engine.connect() as conn:
        row = conn.execute(
            sa_select(alias).where(alias.c.address == "team-owner@lists.example.org")
        ).fetchone()
    assert row is not None
    assert row.goto == "alice@example.org,bob@example.org"
    assert row.domain == "lists.example.org"
    assert int(row.active) == 1


def test_delete_owner_alias_removes_row() -> None:
    """_delete_owner_alias removes the alias row written by _write_owner_alias."""
    md = _fake_metadata()
    svc, engine = _service(md, _fake_adapter())
    alias = md.tables["alias"]
    from sqlalchemy import select as sa_select

    with engine.begin() as conn:
        svc._write_owner_alias(  # pyright: ignore[reportPrivateUsage]  # WHY: testing private helper directly
            conn,
            "team@lists.example.org",
            ["alice@example.org"],
        )
    with engine.begin() as conn:
        svc._delete_owner_alias(conn, "team@lists.example.org")  # pyright: ignore[reportPrivateUsage]  # WHY: testing private helper directly
    with engine.connect() as conn:
        row = conn.execute(
            sa_select(alias).where(alias.c.address == "team-owner@lists.example.org")
        ).fetchone()
    assert row is None
