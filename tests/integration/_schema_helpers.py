"""Shared helpers for `postino schema migrate` integration tests.

Used by both ``test_schema_migrate.py`` and
``test_schema_version_migration.py``. Both files exercise the same CLI
entry point, so they share:

- ``invoke_migrate()`` — invokes the schema sub-app via Typer's
  ``CliRunner`` with a synthetic postfix ``sql-virtual_mailbox_maps.cf``
  derived from ``POSTINO_TEST_DB_URL``.
- ``ensure_routes_present_after`` — autouse fixture that re-creates the
  ``routes`` table after each test, so subsequent tests in the
  integration suite can still reflect the PA schema.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from typer.testing import CliRunner

from postino.commands.schema import app as schema_app

_ROUTES_DDL = (
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


def engine_from_env() -> Engine:
    """Return an engine pointed at ``POSTINO_TEST_DB_URL`` or skip the test."""
    url = os.environ.get("POSTINO_TEST_DB_URL")
    if not url:
        pytest.skip("POSTINO_TEST_DB_URL not set")
    return create_engine(url)


def routes_exists(engine: Engine) -> bool:
    """Return True iff the ``routes`` table exists in the current schema."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables"
                " WHERE table_schema = DATABASE() AND table_name = 'routes'"
            )
        ).scalar()
    return bool(row)


def invoke_migrate() -> int:
    """Run `postino schema migrate` via Typer's CliRunner; returns exit code.

    Invokes the schema sub-app directly (bypassing the root ``_entry``
    callback that calls ``build_services`` → ``reflect_schema``) and
    sets the minimum env vars that ``_load_settings_for_migrate`` needs.
    ``mailbox_creds()`` reads the postfix sql cf; we point it at a tmp dir
    with a synthetic cf file derived from ``POSTINO_TEST_DB_URL``.
    """
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
def ensure_routes_present_after(  # type: ignore[misc]  # WHY: autouse pytest fixture is called by pytest, not directly by test code; pyright reports reportUnusedFunction.
    request: pytest.FixtureRequest,
) -> Generator[None, None, None]:
    """After each test, make sure the routes table is re-created so the rest
    of the integration suite can reflect it without failing.

    Probes with ``routes_exists`` first so we only re-create when the
    migrate test actually dropped it — keeps the post-condition tight.
    """
    yield
    engine = engine_from_env()
    try:
        if not routes_exists(engine):
            with engine.begin() as conn:
                conn.execute(text(_ROUTES_DDL))
    finally:
        engine.dispose()
