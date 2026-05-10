"""Fixtures for the subprocess-driven e2e CLI test suite.

Strategy: the postino CLI loads PostinoSettings via POSTINO_* env vars
(highest priority in pydantic-settings). We set them in the subprocess
env to point at the test DB, a temp mail root, and a fake hook script.
No TOML file is needed — env vars override the TOML paths entirely.

DB credentials come from POSTINO_TEST_DB_URL (via `make_postfix_cf` which
writes the creds into postfix-style sql-*.cf files, the only credential
path the CLI accepts in production).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime

import pytest
from sqlalchemy import MetaData, text
from sqlalchemy.engine import Engine

from tests.cli.test_user_cmd import env_for_cli, make_postfix_cf

# ---------------------------------------------------------------------------
# DB seed helpers
# ---------------------------------------------------------------------------

_FROZEN_DT = "2026-05-09 12:00:00"


def _seed_domain(conn: object, md: MetaData, domain: str) -> None:
    """Insert a minimal real domain row."""
    import sqlalchemy

    assert isinstance(conn, sqlalchemy.engine.Connection)
    conn.execute(
        md.tables["domain"]
        .insert()
        .values(
            domain=domain,
            description=f"e2e test domain {domain}",
            aliases=10,
            mailboxes=10,
            maxquota=0,
            quota=1073741824,
            transport="virtual",
            backupmx=0,
            created=_FROZEN_DT,
            modified=_FROZEN_DT,
            active=1,
        )
    )


def _seed_mailbox(conn: object, md: MetaData, username: str, domain: str) -> None:
    """Insert a minimal mailbox + quota2 row."""
    import sqlalchemy

    assert isinstance(conn, sqlalchemy.engine.Connection)
    local_part, _, _ = username.partition("@")
    conn.execute(
        md.tables["mailbox"]
        .insert()
        .values(
            username=username,
            password="{NOAUTH}",
            name=f"Test {local_part}",
            maildir=f"{domain}/{local_part}/",
            quota=0,
            local_part=local_part,
            domain=domain,
            active=1,
            created=_FROZEN_DT,
            modified=_FROZEN_DT,
        )
    )
    conn.execute(
        md.tables["quota2"].insert().values(username=username, bytes=1024 * 1024, messages=3)
    )


def _seed_alias(conn: object, md: MetaData, address: str, goto: str, domain: str) -> None:
    """Insert an alias row."""
    import sqlalchemy

    assert isinstance(conn, sqlalchemy.engine.Connection)
    conn.execute(
        md.tables["alias"]
        .insert()
        .values(
            address=address,
            goto=goto,
            domain=domain,
            created=_FROZEN_DT,
            modified=_FROZEN_DT,
            active=1,
        )
    )


def _seed_pa_all_row(conn: object, md: MetaData) -> None:
    """Insert PostfixAdmin's 'ALL' pseudo-row (the regression trigger)."""
    import sqlalchemy

    assert isinstance(conn, sqlalchemy.engine.Connection)
    conn.execute(
        md.tables["domain"]
        .insert()
        .values(
            domain="ALL",
            description="",
            aliases=0,
            mailboxes=0,
            maxquota=0,
            quota=0,
            transport="",  # empty transport — the crash trigger
            backupmx=0,
            created=_FROZEN_DT,
            modified=_FROZEN_DT,
            active=1,
        )
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def e2e_db(integration_engine: Engine) -> Iterator[Engine]:
    """Per-module DB state: seed two domains, mailboxes, aliases, and the PA ALL row.

    The module-scope means all tests in test_cli_e2e.py share the same seed
    data (read-only commands only). We truncate at the start to guarantee a
    clean slate regardless of prior test runs.
    """
    md = MetaData()
    md.reflect(bind=integration_engine)

    with integration_engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for tbl in md.sorted_tables:
            conn.execute(text(f"TRUNCATE TABLE {tbl.name}"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))

    with integration_engine.begin() as conn:
        # PA ALL pseudo-row (the crash regression trigger)
        _seed_pa_all_row(conn, md)

        # Two real domains
        for dom in ("alpha.example.com", "beta.example.com"):
            _seed_domain(conn, md, dom)

        # 3 mailboxes per domain
        for dom in ("alpha.example.com", "beta.example.com"):
            for user in ("alice", "bob", "carol"):
                _seed_mailbox(conn, md, f"{user}@{dom}", dom)

        # A few aliases
        _seed_alias(
            conn,
            md,
            "info@alpha.example.com",
            "alice@alpha.example.com",
            "alpha.example.com",
        )
        _seed_alias(
            conn,
            md,
            "info@beta.example.com",
            "bob@beta.example.com",
            "beta.example.com",
        )

    yield integration_engine


@pytest.fixture(scope="module")
def e2e_env(
    e2e_db: Engine,
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, str]:
    """Environment dict for subprocess calls against the e2e DB.

    Written once per module; all tests reuse the same temp dirs.
    Uses the same env var + postfix-cf-file mechanism as existing CLI tests.
    """
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    tmp = tmp_path_factory.mktemp("e2e_cli")
    sql_dir = tmp / "postfix"
    mail_root = tmp / "mail"
    hook = tmp / "hook.sh"

    make_postfix_cf(db_url, sql_dir)
    mail_root.mkdir()
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)

    return env_for_cli(db_url, mail_root, hook, sql_dir)


@pytest.fixture
def frozen_clock() -> datetime:
    return datetime(2026, 5, 9, 12, 0, 0)
