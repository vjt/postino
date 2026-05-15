"""Integration tests for _check_db_grants against the real test DB.

The test fabricates a low-privilege user, runs the check, then drops
the user. Requires POSTINO_TEST_DB_URL pointing at a DB whose user can
CREATE USER + GRANT.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from postino_core.check.consistency import (
    _check_db_grants,  # pyright: ignore[reportPrivateUsage]  # WHY: integration-testing private grant-check function.
)
from postino_core.config import PostinoSettings
from postino_core.enums import IdentityBackend, PasswordScheme


def _settings(tmp_path: Path) -> PostinoSettings:
    return PostinoSettings(
        identity_backend=IdentityBackend.LOCAL,
        postfix_sql_dir=tmp_path / "postfix",
        virtual_mailbox_base=tmp_path / "mail",
        postcreation_hook=Path("/bin/true"),
        vmail_uid=5000,
        vmail_gid=5000,
        default_password_scheme=PasswordScheme.BCRYPT,
        default_quota_bytes=1024**3,
    )


def _engine_for_user(admin_url: str, user: str, password: str, db: str) -> Engine:
    """Build an Engine URL for `user` using the admin URL's host/port."""
    admin = create_engine(admin_url)
    host = admin.url.host or "localhost"
    port = admin.url.port or 3306
    return create_engine(
        f"mysql+pymysql://{user}:{password}@{host}:{port}/{db}",
        pool_pre_ping=True,
    )


@pytest.fixture
def low_priv_user(db: Engine) -> Iterator[tuple[Engine, str]]:
    """Create a user with the exact required grants. Drop on teardown."""
    user = "postino_grants_test"
    password = "ITPbqB3pZ8fGmMQ2"
    db_name = db.url.database or "postfix"
    with db.begin() as conn:
        conn.execute(text(f"DROP USER IF EXISTS '{user}'@'%'"))
        conn.execute(text(f"CREATE USER '{user}'@'%' IDENTIFIED BY '{password}'"))
        for tbl in ("mailbox", "alias", "alias_domain", "domain", "quota2"):
            conn.execute(
                text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON `{db_name}`.`{tbl}` TO '{user}'@'%'")
            )
        conn.execute(text(f"GRANT SELECT, INSERT ON `{db_name}`.`log` TO '{user}'@'%'"))
    user_engine = _engine_for_user(str(db.url), user, password, db_name)
    yield user_engine, db_name
    user_engine.dispose()
    with db.begin() as conn:
        conn.execute(text(f"DROP USER IF EXISTS '{user}'@'%'"))


@pytest.mark.integration
def test_check_db_grants_real_low_priv_user_emits_info(
    low_priv_user: tuple[Engine, str], tmp_path: Path
) -> None:
    engine, _ = low_priv_user
    findings = _check_db_grants(_settings(tmp_path), engine)
    severities = [f.severity for f in findings]
    assert "error" not in severities, [(f.name, f.severity, f.message) for f in findings]


@pytest.mark.integration
def test_check_db_grants_real_missing_priv_emits_error(
    low_priv_user: tuple[Engine, str], db: Engine, tmp_path: Path
) -> None:
    engine, db_name = low_priv_user
    # Revoke INSERT on mailbox from the test user.
    with db.begin() as conn:
        conn.execute(text(f"REVOKE INSERT ON `{db_name}`.`mailbox` FROM 'postino_grants_test'@'%'"))
    findings = _check_db_grants(_settings(tmp_path), engine)
    mailbox_err = next((f for f in findings if f.name == "db_grants:mailbox"), None)
    assert mailbox_err is not None
    assert mailbox_err.severity == "error"
    assert "INSERT" in mailbox_err.message
    # Restore grant so other tests see consistent state if reused.
    with db.begin() as conn:
        conn.execute(text(f"GRANT INSERT ON `{db_name}`.`mailbox` TO 'postino_grants_test'@'%'"))
