"""postino schema migrate creates postino_schema_version + upserts row.

Requires POSTINO_TEST_DB_URL pointing at a live MariaDB/MySQL instance
with a PostfixAdmin-compatible schema (see tests/fixtures/postfixadmin.sql).
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from postino.commands.schema import CURRENT_SCHEMA_VERSION

from ._schema_helpers import (
    ensure_routes_present_after,  # noqa: F401  # pyright: ignore[reportUnusedImport]  # WHY: autouse pytest fixture re-exported into this module's namespace; pytest discovers it via module globals, not direct test references.
    invoke_migrate,
)

pytestmark = pytest.mark.integration


def test_migrate_creates_version_table(db: Engine) -> None:
    code = invoke_migrate()
    assert code == 0, f"migrate exited {code}, expected 0"
    with db.connect() as conn:
        v = conn.execute(text("SELECT version FROM postino_schema_version")).scalar_one()
    assert str(v) == CURRENT_SCHEMA_VERSION


def test_migrate_is_idempotent_single_row(db: Engine) -> None:
    assert invoke_migrate() == 0
    assert invoke_migrate() == 0
    with db.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM postino_schema_version")).scalar_one()
    assert n == 1  # UPSERT — never appends
