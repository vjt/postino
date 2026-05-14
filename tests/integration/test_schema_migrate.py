"""Integration tests for `postino schema migrate`.

Requires POSTINO_TEST_DB_URL pointing at a live MariaDB/MySQL instance
with a PostfixAdmin-compatible schema (see tests/fixtures/postfixadmin.sql).

Run with:
    pytest tests/integration/test_schema_migrate.py -x -v
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def _engine() -> Engine:
    url = os.environ.get("POSTINO_TEST_DB_URL")
    if not url:
        pytest.skip("POSTINO_TEST_DB_URL not set")
    return create_engine(url)


def _routes_exists(engine: Engine) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables"
                " WHERE table_schema = DATABASE() AND table_name = 'routes'"
            )
        ).scalar()
    return bool(row)


def _invoke_migrate() -> int:
    """Run `postino schema migrate` via Typer's CliRunner; returns exit code.

    Invokes the schema sub-app directly (bypassing the root ``_entry``
    callback that calls ``build_services`` → ``reflect_schema``) and
    sets the minimum env vars that ``_load_settings_for_migrate`` needs.
    ``mailbox_creds()`` reads the postfix sql cf; we point it at a tmp dir
    with a synthetic cf file derived from ``POSTINO_TEST_DB_URL``.
    """
    import tempfile

    from typer.testing import CliRunner

    from postino.commands.schema import app as schema_app

    db_url = os.environ.get("POSTINO_TEST_DB_URL", "")
    # Parse db_url: mysql+pymysql://user:pass@host/dbname
    body = db_url.replace("mysql+pymysql://", "")
    auth, _, hostdb = body.partition("@")
    user, _, pwd = auth.partition(":")
    host, _, dbname = hostdb.partition("/")
    # Strip port suffix (mirrors make_postfix_cf in tests/cli/test_user_cmd.py)
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
        # schema_app has a single command (migrate); Typer promotes it to
        # the root so no subcommand name is passed.
        result = runner.invoke(schema_app, [])

    return result.exit_code


@pytest.fixture(autouse=True)
def _ensure_routes_present_after(  # type: ignore[misc]  # WHY: autouse pytest fixture is called by pytest, not directly by test code; pyright reports reportUnusedFunction.
    request: pytest.FixtureRequest,
) -> Generator[None, None, None]:
    """After each test, make sure the routes table is re-created so the rest
    of the integration suite can reflect it without failing."""
    yield
    engine = _engine()
    try:
        if not _routes_exists(engine):
            with engine.begin() as conn:
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
        engine.dispose()


def test_migrate_creates_routes_table_when_missing() -> None:
    """migrate creates the routes table when it does not exist yet."""
    engine = _engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS `routes`"))
        assert not _routes_exists(engine), "setup: routes should be gone"
    finally:
        engine.dispose()

    code = _invoke_migrate()
    assert code == 0, f"migrate exited {code}, expected 0"

    engine2 = _engine()
    try:
        assert _routes_exists(engine2), "routes table was not created by migrate"
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
    code1 = _invoke_migrate()
    assert code1 == 0, f"first migrate run exited {code1}"

    engine = _engine()
    try:
        assert _routes_exists(engine), "routes not present after first migrate"
    finally:
        engine.dispose()

    # Second run — must also succeed with table already present.
    code2 = _invoke_migrate()
    assert code2 == 0, f"second migrate run exited {code2}"

    engine2 = _engine()
    try:
        assert _routes_exists(engine2), "routes disappeared after second migrate"
    finally:
        engine2.dispose()
