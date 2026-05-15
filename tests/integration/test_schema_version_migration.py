"""postino schema migrate creates postino_schema_version + upserts row.

Requires POSTINO_TEST_DB_URL pointing at a live MariaDB/MySQL instance
with a PostfixAdmin-compatible schema (see tests/fixtures/postfixadmin.sql).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from typer.testing import CliRunner

from postino.commands.schema import (
    _CURRENT_SCHEMA_VERSION,  # type: ignore[reportPrivateUsage]  # WHY: test pins the module-private current schema version constant; importing it ensures the assertion is the single source of truth.
)
from postino.commands.schema import app as schema_app

pytestmark = pytest.mark.integration


def _invoke_migrate() -> int:
    """Run `postino schema migrate` via Typer's CliRunner; returns exit code.

    Mirrors tests/integration/test_schema_migrate.py: synthesises a postfix
    sql-virtual_mailbox_maps.cf from POSTINO_TEST_DB_URL and sets the env
    vars _load_settings_for_migrate needs.
    """
    db_url = os.environ.get("POSTINO_TEST_DB_URL", "")
    body = db_url.replace("mysql+pymysql://", "")
    auth, _, hostdb = body.partition("@")
    user, _, pwd = auth.partition(":")
    host, _, dbname = hostdb.partition("/")
    host, _, _port = host.partition(":")

    with tempfile.TemporaryDirectory() as sql_dir_str:
        sql_dir = Path(sql_dir_str)
        cf_body = f"hosts = {host}\nuser = {user}\npassword = {pwd}\ndbname = {dbname}\n"
        cf_file = sql_dir / "sql-virtual_mailbox_maps.cf"
        cf_file.write_text(cf_body)
        cf_file.chmod(0o600)

        runner = CliRunner(
            env={
                "POSTINO_IDENTITY_BACKEND": "local",
                "POSTINO_POSTFIX_SQL_DIR": sql_dir_str,
                "POSTINO_VIRTUAL_MAILBOX_BASE": "/tmp/postino-test-migrate",
                "POSTINO_POSTCREATION_HOOK": "/bin/true",
                "POSTINO_VMAIL_UID": "-1",
                "POSTINO_VMAIL_GID": "-1",
                "POSTINO_DEFAULT_PASSWORD_SCHEME": "BLF-CRYPT",
                "POSTINO_DEFAULT_QUOTA_BYTES": "1073741824",
            }
        )
        result = runner.invoke(schema_app, [])

    return result.exit_code


@pytest.fixture(autouse=True)
def _restore_routes_table(  # type: ignore[misc]  # WHY: autouse pytest fixture is consumed by pytest, not test code; pyright flags reportUnusedFunction.
    request: pytest.FixtureRequest,
) -> Generator[None, None, None]:
    """After each test, recreate the routes table so other integration tests
    that rely on schema reflection don't fail in this session."""
    yield
    url = os.environ.get("POSTINO_TEST_DB_URL")
    if not url:
        return
    from sqlalchemy import create_engine

    eng = create_engine(url)
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS `routes` ("
                    "  `pattern`      VARCHAR(255) NOT NULL,"
                    "  `transport`    VARCHAR(64)  NOT NULL,"
                    "  `domain`       VARCHAR(255) NOT NULL,"
                    "  `list_address` VARCHAR(255) DEFAULT NULL,"
                    "  `priority`     SMALLINT(6)  NOT NULL DEFAULT 50,"
                    "  `active`       TINYINT(1)   NOT NULL DEFAULT 1,"
                    "  `created`      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                    "  PRIMARY KEY (`pattern`),"
                    "  KEY `idx_domain` (`domain`),"
                    "  KEY `idx_list_address` (`list_address`)"
                    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
                )
            )
    finally:
        eng.dispose()


def test_migrate_creates_version_table(db: Engine) -> None:
    code = _invoke_migrate()
    assert code == 0, f"migrate exited {code}, expected 0"
    with db.connect() as conn:
        v = conn.execute(text("SELECT version FROM postino_schema_version")).scalar_one()
    assert str(v) == _CURRENT_SCHEMA_VERSION


def test_migrate_is_idempotent_single_row(db: Engine) -> None:
    assert _invoke_migrate() == 0
    assert _invoke_migrate() == 0
    with db.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM postino_schema_version")).scalar_one()
    assert n == 1  # UPSERT — never appends
