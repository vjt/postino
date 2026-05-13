"""Integration tests for routes table reflection + RoutesRepository.

Requires POSTINO_TEST_DB_URL pointing at a MariaDB schema with the v0.10
routes table loaded (see tests/fixtures/postfixadmin.sql).
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from postino_core.db import reflect_schema
from postino_core.repos.routes import RoutesRepository

pytestmark = pytest.mark.integration


def _engine() -> Engine:
    url = os.environ.get("POSTINO_TEST_DB_URL")
    if not url:
        pytest.skip("POSTINO_TEST_DB_URL not set")
    return create_engine(url)


def test_routes_table_reflected() -> None:
    engine = _engine()
    md = reflect_schema(engine)
    assert "routes" in md.tables
    cols = {c.name for c in md.tables["routes"].columns}
    assert cols == {
        "pattern",
        "transport",
        "domain",
        "list_address",
        "priority",
        "active",
        "created",
    }


def test_routes_repository_round_trip() -> None:
    engine = _engine()
    md = reflect_schema(engine)
    repo = RoutesRepository(engine=engine, metadata=md)

    test_addr = "v010smoke@v010-test.example.org"
    # Clean up any leftover rows from a prior failed run.
    with engine.begin() as conn:
        repo.delete_by_list_address(conn, test_addr)

    try:
        with engine.begin() as conn:
            repo.insert_mlmmj_list(conn, test_addr)
        with engine.connect() as conn:
            rows = repo.list_by_list_address(conn, test_addr)
        assert len(rows) == 5
        assert {r.transport for r in rows} == {
            "mlmmj-bounce:",
            "mlmmj-sub:",
            "mlmmj-unsub:",
            "mlmmj-help:",
            "mlmmj-receive:",
        }
    finally:
        with engine.begin() as conn:
            n = repo.delete_by_list_address(conn, test_addr)
        assert n == 5
