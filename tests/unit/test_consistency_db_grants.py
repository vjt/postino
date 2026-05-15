"""Unit tests for _check_db_grants — stubs _parse_show_grants so we
don't need a live DB. Integration test in tests/integration/."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from postino_core.check.consistency import (
    _check_db_grants,  # pyright: ignore[reportPrivateUsage]  # WHY: testing private grant-check function from inside the package.
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


def _stub_engine(db_name: str, rows: list[str]) -> MagicMock:
    """Build a MagicMock engine that returns `rows` from SHOW GRANTS."""
    engine = MagicMock()
    engine.url.database = db_name
    conn_ctx = engine.connect.return_value.__enter__.return_value
    result = MagicMock()
    # SHOW GRANTS returns one-col rows; .all() yields list of (str,)
    result.all.return_value = [(line,) for line in rows]
    conn_ctx.execute.return_value = result
    return engine


_EXACT_REQUIRED_ROWS = [
    "GRANT USAGE ON *.* TO `postino`@`%`",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON `postfix`.`mailbox` TO `postino`@`%`",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON `postfix`.`alias` TO `postino`@`%`",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON `postfix`.`alias_domain` TO `postino`@`%`",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON `postfix`.`domain` TO `postino`@`%`",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON `postfix`.`quota2` TO `postino`@`%`",
    "GRANT SELECT, INSERT ON `postfix`.`log` TO `postino`@`%`",
]


def test_db_grants_exact_required_emits_info_only(tmp_path: Path) -> None:
    engine = _stub_engine("postfix", _EXACT_REQUIRED_ROWS)
    findings = _check_db_grants(_settings(tmp_path), engine)
    assert all(f.severity == "info" for f in findings), [
        (f.name, f.severity, f.message) for f in findings
    ]
    assert any(f.name == "db_grants" for f in findings)
