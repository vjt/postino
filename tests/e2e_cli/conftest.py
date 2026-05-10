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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import MetaData, text
from sqlalchemy.engine import Engine

from tests.cli.test_user_cmd import env_for_cli, make_postfix_cf

# ---------------------------------------------------------------------------
# DB seed helpers
# ---------------------------------------------------------------------------

_FROZEN_DT = "2026-05-09 12:00:00"


def _seed_domain(conn: object, md: MetaData, domain: str, *, transport: str = "virtual") -> None:
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
            transport=transport,
            backupmx=0,
            created=_FROZEN_DT,
            modified=_FROZEN_DT,
            active=1,
        )
    )


def _seed_mailbox(
    conn: object,
    md: MetaData,
    username: str,
    domain: str,
    *,
    active: int = 1,
    quota: int = 0,
) -> None:
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
            quota=quota,
            local_part=local_part,
            domain=domain,
            active=active,
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


def _truncate_all(engine: Engine, md: MetaData) -> None:
    """TRUNCATE every PA table.  Called at the start of module and per-write fixture."""
    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for tbl in md.sorted_tables:
            conn.execute(text(f"TRUNCATE TABLE {tbl.name}"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))


# ---------------------------------------------------------------------------
# Fixtures — read-only tests (module-scoped seed)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def e2e_db(integration_engine: Engine) -> Iterator[Engine]:
    """Per-module DB state: seed a complete PA-style fixture.

    The module-scope means all tests in test_cli_e2e.py share the same seed
    data for read-only commands.  Truncates at the start to guarantee a
    clean slate regardless of prior test runs.

    Seed layout:
    - PA ALL pseudo-row (regression trigger)
    - alpha.example.com (virtual)
    - beta.example.com  (virtual)
    - lmtp.example.io   (lmtp:unix:private/dovecot-lmtp — transport variant)
    - 3 mailboxes in alpha (alice, bob, carol); alice disabled
    - 3 mailboxes in beta  (alice, bob, carol)
    - 1 mailbox  in lmtp   (dave) with non-zero quota
    - multi-target alias in alpha  (team@alpha.example.com → alice,bob)
    - simple alias in beta         (info@beta.example.com → bob)

    Note: catch-all addresses (@domain.com) are intentionally excluded from
    the seed. The Alias.address field is typed EmailStr, which does not accept
    the PA catch-all form (@domain.com — no local-part). This is a known model
    limitation; catch-all support would require relaxing Alias.address to str.
    """
    md = MetaData()
    md.reflect(bind=integration_engine)
    _truncate_all(integration_engine, md)

    with integration_engine.begin() as conn:
        _seed_pa_all_row(conn, md)

        _seed_domain(conn, md, "alpha.example.com")
        _seed_domain(conn, md, "beta.example.com")
        _seed_domain(conn, md, "lmtp.example.io", transport="lmtp:unix:private/dovecot-lmtp")

        # alpha mailboxes — alice disabled, bob + carol active
        _seed_mailbox(conn, md, "alice@alpha.example.com", "alpha.example.com", active=0)
        _seed_mailbox(conn, md, "bob@alpha.example.com", "alpha.example.com")
        _seed_mailbox(conn, md, "carol@alpha.example.com", "alpha.example.com")

        # beta mailboxes — all active
        for user in ("alice", "bob", "carol"):
            _seed_mailbox(conn, md, f"{user}@beta.example.com", "beta.example.com")

        # lmtp domain — dave with a quota set
        _seed_mailbox(conn, md, "dave@lmtp.example.io", "lmtp.example.io", quota=5_368_709_120)
        _seed_alias(
            conn,
            md,
            "team@alpha.example.com",
            "alice@alpha.example.com,bob@alpha.example.com",
            "alpha.example.com",
        )
        _seed_alias(conn, md, "info@beta.example.com", "bob@beta.example.com", "beta.example.com")

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
    # No-op hook that accepts the PA four-arg contract (USERNAME DOMAIN MAILDIR QUOTA).
    # Extra positional args are silently ignored by bash `exit 0`.
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)

    return env_for_cli(db_url, mail_root, hook, sql_dir)


# ---------------------------------------------------------------------------
# Fixtures — write tests (function-scoped: fresh DB state per test)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteEnv:
    """Bundle passed to write-command tests."""

    env: dict[str, str]
    """Subprocess env pointing at the test DB."""
    mail_root: Path
    """Temp directory for maildirs created by the postcreation hook."""
    engine: Engine
    """SQLAlchemy engine for DB-state assertions."""
    metadata: MetaData
    """Reflected schema metadata."""


@pytest.fixture
def e2e_write_env(
    db: Engine,
    tmp_path: Path,
) -> Iterator[WriteEnv]:
    """Function-scoped fixture for write-command tests.

    Depends on the root conftest ``db`` fixture, which TRUNCATEs every PA
    table before yielding (function-scoped).  This keeps the write tests
    isolated from each other AND from the module-scoped ``e2e_db`` seed
    used by the read-only suite in ``test_cli_e2e.py`` — the write tests
    live in a separate module (``test_cli_write_e2e.py``) so the two
    module-scoped fixtures never share a live dataset.

    The postcreation hook creates the maildir tree under ``tmp_path/mail``
    using the PA four-arg contract (USERNAME DOMAIN MAILDIR QUOTA);
    pytest auto-cleans tmp_path after each test.
    """
    md = MetaData()
    md.reflect(bind=db)

    db_url = os.environ["POSTINO_TEST_DB_URL"]
    sql_dir = tmp_path / "postfix"
    mail_root = tmp_path / "mail"
    hook = tmp_path / "hook.sh"

    make_postfix_cf(db_url, sql_dir)
    mail_root.mkdir()

    # Hook: accepts USERNAME DOMAIN MAILDIR QUOTA (PA contract).
    # Creates the maildir tree under mail_root so the FS adapter
    # finds a consistent state after user add.
    hook.write_text(
        "#!/bin/sh\n"
        "USERNAME=$1; DOMAIN=$2; MAILDIR=$3\n"
        f'mkdir -p "{mail_root}/$MAILDIR/cur" "{mail_root}/$MAILDIR/new"'
        f' "{mail_root}/$MAILDIR/tmp"\n'
        "exit 0\n"
    )
    hook.chmod(0o755)

    env = env_for_cli(db_url, mail_root, hook, sql_dir)

    # Seed a write-test domain structure
    with db.begin() as conn:
        _seed_pa_all_row(conn, md)
        _seed_domain(conn, md, "write.example.com")
        _seed_domain(conn, md, "other.example.com")
        _seed_mailbox(conn, md, "existing@write.example.com", "write.example.com")
        _seed_mailbox(conn, md, "disabled@write.example.com", "write.example.com", active=0)
        _seed_alias(
            conn,
            md,
            "info@write.example.com",
            "existing@write.example.com",
            "write.example.com",
        )

    yield WriteEnv(env=env, mail_root=mail_root, engine=db, metadata=md)


@pytest.fixture
def frozen_clock() -> datetime:
    return datetime(2026, 5, 9, 12, 0, 0)
