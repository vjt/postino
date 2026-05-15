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


def test_db_grants_missing_insert_on_mailbox_errors(tmp_path: Path) -> None:
    rows = _EXACT_REQUIRED_ROWS.copy()
    # Replace the mailbox row with one that's missing INSERT.
    rows[1] = "GRANT SELECT, UPDATE, DELETE ON `postfix`.`mailbox` TO `postino`@`%`"
    engine = _stub_engine("postfix", rows)
    findings = _check_db_grants(_settings(tmp_path), engine)
    mailbox_err = next(f for f in findings if f.name == "db_grants:mailbox")
    assert mailbox_err.severity == "error"
    assert "INSERT" in mailbox_err.message


def test_db_grants_all_privileges_on_db_warns_overprivileged(tmp_path: Path) -> None:
    rows = [
        "GRANT USAGE ON *.* TO `postino`@`%`",
        "GRANT ALL PRIVILEGES ON `postfix`.* TO `postino`@`%`",
    ]
    engine = _stub_engine("postfix", rows)
    findings = _check_db_grants(_settings(tmp_path), engine)
    # No missing-priv errors (ALL covers everything).
    assert all(
        f.name != f"db_grants:{t}" for f in findings for t in ("mailbox", "alias", "log")
    )
    # But log only needs SELECT+INSERT; ALL gives UPDATE+DELETE → overpriv warn.
    over = next(f for f in findings if f.name == "db_grants:overprivileged")
    assert over.severity == "warn"


def test_db_grants_no_scope_for_db_errors(tmp_path: Path) -> None:
    rows = [
        "GRANT USAGE ON *.* TO `postino`@`%`",
        "GRANT SELECT ON `other_db`.* TO `postino`@`%`",
    ]
    engine = _stub_engine("postfix", rows)
    findings = _check_db_grants(_settings(tmp_path), engine)
    assert len(findings) == 1
    assert findings[0].name == "db_grants"
    assert findings[0].severity == "error"
    assert "no GRANT rows match db" in findings[0].message


def test_db_grants_global_all_privileges_passes_with_overpriv_warn(tmp_path: Path) -> None:
    rows = ["GRANT ALL PRIVILEGES ON *.* TO `root`@`localhost` WITH GRANT OPTION"]
    engine = _stub_engine("postfix", rows)
    findings = _check_db_grants(_settings(tmp_path), engine)
    # No missing-priv errors.
    assert not any(
        f.name.startswith("db_grants:") and f.severity == "error" for f in findings
    )
    # Should warn (root has way more than postino needs on log).
    assert any(f.name == "db_grants:overprivileged" for f in findings)
