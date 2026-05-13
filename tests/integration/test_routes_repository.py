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
    assert cols == {"pattern", "transport", "domain", "list_address", "priority", "active", "created"}
