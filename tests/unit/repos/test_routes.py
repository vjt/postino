"""Unit tests for RoutesRepository — pattern generation + repo CRUD.

Most tests bind to an SQLite-backed reflected schema fixture for speed;
the integration test suite covers MariaDB compatibility separately.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import Column, MetaData, SmallInteger, String, Table, create_engine
from sqlalchemy.dialects.sqlite import BOOLEAN

from postino_core.repos.routes import (
    Route,
    RoutesRepository,
    _mlmmj_patterns,  # pyright: ignore[reportPrivateUsage]  # WHY: module-private helper exercised directly to assert pattern-generation contract
)


def test_mlmmj_patterns_emits_five_rows() -> None:
    rows = _mlmmj_patterns("team@lists.example.org")
    assert len(rows) == 5
    patterns = {p for p, _t, _pri in rows}
    transports = {t for _p, t, _pri in rows}
    priorities = {pri for _p, _t, pri in rows}
    assert transports == {
        "mlmmj-bounce:",
        "mlmmj-sub:",
        "mlmmj-unsub:",
        "mlmmj-help:",
        "mlmmj-receive:",
    }
    assert priorities == {10, 50}  # 4x priority 10 + 1x priority 50
    # localpart-anchored, not domain-wide
    assert any(p == r"^team-bounces@lists\.example\.org$" for p in patterns)
    assert any(p == r"^team-confirm-sub-.+@lists\.example\.org$" for p in patterns)
    assert any(p == r"^team-confirm-unsub-.+@lists\.example\.org$" for p in patterns)
    assert any(p == r"^team-help@lists\.example\.org$" for p in patterns)
    # catchall absorbs +ext for plus-addressing
    assert any(p == r"^team(\+.+)?@lists\.example\.org$" for p in patterns)


def test_mlmmj_patterns_escapes_regex_metacharacters_in_address() -> None:
    # local-part `+` is invalid for a list name but other metacharacters
    # like `.` must be escaped. Real-world example: `team.b@lists.example.org`.
    rows = _mlmmj_patterns("team.b@lists.example.org")
    patterns = {p for p, _t, _pri in rows}
    # the literal `.` in the localpart must be `\.` in the regex
    assert r"^team\.b-bounces@lists\.example\.org$" in patterns
    assert r"^team\.b(\+.+)?@lists\.example\.org$" in patterns


def test_mlmmj_patterns_rejects_invalid_address() -> None:
    with pytest.raises(ValueError):
        _mlmmj_patterns("no-at-sign")
    with pytest.raises(ValueError):
        _mlmmj_patterns("@no-localpart.example.org")
    with pytest.raises(ValueError):
        _mlmmj_patterns("localpart@")


def test_route_model_frozen_strict() -> None:
    r = Route(
        pattern=r"^team@lists\.example\.org$",
        transport="mlmmj-receive:",
        domain="lists.example.org",
        list_address="team@lists.example.org",
        priority=50,
        active=True,
    )
    assert r.pattern == r"^team@lists\.example\.org$"
    assert r.transport == "mlmmj-receive:"
    with pytest.raises(ValidationError):
        r.priority = 10  # type: ignore[misc]  # WHY: testing frozen model rejection at runtime


def _fake_metadata() -> MetaData:
    """Build an in-memory SQLite metadata that mirrors the routes table
    (SQLite types only; integration tests cover MariaDB type fidelity)."""
    md = MetaData()
    Table(
        "routes",
        md,
        Column("pattern", String(255), primary_key=True),
        Column("transport", String(64), nullable=False),
        Column("domain", String(255), nullable=False),
        Column("list_address", String(255), nullable=True),
        Column("priority", SmallInteger, nullable=False, default=50),
        Column("active", BOOLEAN, nullable=False, default=True),
    )
    return md


def test_insert_mlmmj_list_writes_five_rows() -> None:
    engine = create_engine("sqlite:///:memory:")
    md = _fake_metadata()
    md.create_all(engine)
    repo = RoutesRepository(engine=engine, metadata=md)

    with engine.begin() as conn:
        repo.insert_mlmmj_list(conn, "team@lists.example.org")  # type: ignore[arg-type]  # WHY: EmailStr accepts str at the test boundary; same pattern as src/postino/commands/list.py allowlist entries.

    with engine.connect() as conn:
        rows = conn.execute(md.tables["routes"].select()).fetchall()
    assert len(rows) == 5
    transports = {r._mapping["transport"] for r in rows}  # pyright: ignore[reportPrivateUsage]  # WHY: SQLAlchemy Row._mapping is public API despite the underscore prefix.
    assert transports == {
        "mlmmj-bounce:",
        "mlmmj-sub:",
        "mlmmj-unsub:",
        "mlmmj-help:",
        "mlmmj-receive:",
    }
    list_addresses = {r._mapping["list_address"] for r in rows}  # pyright: ignore[reportPrivateUsage]  # WHY: SQLAlchemy Row._mapping is public API despite the underscore prefix.
    assert list_addresses == {"team@lists.example.org"}
    domains = {r._mapping["domain"] for r in rows}  # pyright: ignore[reportPrivateUsage]  # WHY: SQLAlchemy Row._mapping is public API despite the underscore prefix.
    assert domains == {"lists.example.org"}
