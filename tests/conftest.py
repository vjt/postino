"""Pytest fixtures for postino.

Integration tests require POSTINO_TEST_DB_URL pointing at a MySQL/MariaDB
schema where the test runner has full privileges. The schema is wiped
before each test by truncating every table the PA schema declared."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.engine import Engine

FIXTURE_SQL = Path(__file__).parent / "fixtures" / "postfixadmin.sql"


def _test_db_url() -> str | None:
    return os.environ.get("POSTINO_TEST_DB_URL")


_SQL_STMT_START = ("CREATE", "ALTER", "DROP", "INSERT", "SET", "LOCK", "UNLOCK", "USE", "/*")


@pytest.fixture(scope="session")
def integration_engine() -> Iterator[Engine]:
    url = _test_db_url()
    if url is None:
        pytest.skip("POSTINO_TEST_DB_URL not set — skipping integration tests")
    engine = create_engine(url, future=True)
    # Load schema once per session. Tolerate preamble noise (e.g. mysqldump
    # warnings on stderr-merged dumps) by skipping statements that don't
    # start with a known SQL keyword.
    schema_sql = FIXTURE_SQL.read_text()
    with engine.begin() as conn:
        for stmt in schema_sql.split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            if not stmt.upper().startswith(_SQL_STMT_START):
                continue
            conn.execute(text(stmt))
    yield engine
    engine.dispose()


@pytest.fixture
def db(integration_engine: Engine) -> Iterator[Engine]:
    """Per-test fixture: TRUNCATE every PA table before yielding."""
    md = MetaData()
    md.reflect(bind=integration_engine)
    with integration_engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for tbl in md.sorted_tables:
            conn.execute(text(f"TRUNCATE TABLE {tbl.name}"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    yield integration_engine


@pytest.fixture
def frozen_clock() -> datetime:
    """Deterministic timestamp for created/modified columns."""
    return datetime(2026, 5, 9, 12, 0, 0)


@pytest.fixture
def tmp_mail_root(tmp_path: Path) -> Path:
    """Per-test temporary directory for maildirs."""
    root = tmp_path / "mail"
    root.mkdir()
    return root


@pytest.fixture
def fake_postcreation_hook(tmp_path: Path) -> Path:
    """An executable script that records its argv to a file but always exits 0."""
    log = tmp_path / "hook.log"
    script = tmp_path / "hook.sh"
    script.write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit 0\n')
    script.chmod(0o755)
    return script
