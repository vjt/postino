"""Integration tests for `postino schema migrate`.

Requires POSTINO_TEST_DB_URL pointing at a live MariaDB/MySQL instance
with a PostfixAdmin-compatible schema (see tests/fixtures/postfixadmin.sql).

Run with:
    pytest tests/integration/test_schema_migrate.py -x -v
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from ._schema_helpers import (
    engine_from_env,
    ensure_routes_present_after,  # noqa: F401  # pyright: ignore[reportUnusedImport]  # WHY: autouse pytest fixture re-exported into this module's namespace; pytest discovers it via module globals, not direct test references.
    invoke_migrate,
    routes_exists,
)

pytestmark = pytest.mark.integration


def test_migrate_creates_routes_table_when_missing() -> None:
    """migrate creates the routes table when it does not exist yet."""
    engine = engine_from_env()
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS `routes`"))
        assert not routes_exists(engine), "setup: routes should be gone"
    finally:
        engine.dispose()

    code = invoke_migrate()
    assert code == 0, f"migrate exited {code}, expected 0"

    engine2 = engine_from_env()
    try:
        assert routes_exists(engine2), "routes table was not created by migrate"
        # Verify expected columns are present.
        with engine2.connect() as conn:
            cols = {row[0] for row in conn.execute(text("SHOW COLUMNS FROM `routes`")).fetchall()}
        expected = {
            "pattern",
            "transport",
            "domain",
            "list_address",
            "priority",
            "active",
            "created",
        }
        assert expected == cols
    finally:
        engine2.dispose()


def test_migrate_is_idempotent() -> None:
    """Running migrate twice raises no error and table is still present."""
    # First run — table may or may not exist; both are fine.
    code1 = invoke_migrate()
    assert code1 == 0, f"first migrate run exited {code1}"

    engine = engine_from_env()
    try:
        assert routes_exists(engine), "routes not present after first migrate"
    finally:
        engine.dispose()

    # Second run — must also succeed with table already present.
    code2 = invoke_migrate()
    assert code2 == 0, f"second migrate run exited {code2}"

    engine2 = engine_from_env()
    try:
        assert routes_exists(engine2), "routes disappeared after second migrate"
    finally:
        engine2.dispose()
