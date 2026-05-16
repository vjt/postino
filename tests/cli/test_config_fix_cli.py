"""CLI tests for `postino config fix`.

`fix` is the in-place reconciliation counterpart to `config gen`. The
detection layer (`postino_core.config_gen.fix.detect`) is mocked so
these tests do not need postconf/doveconf binaries on PATH.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from postino.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _detected_clean() -> dict[str, str]:
    return {
        "postfix.config_dir": "/etc/postfix",
        "dovecot.etc_dir": "/etc/dovecot",
        "virtual_mailbox_base": "/var/vmail",
        "virtual_mailbox_maps": "mysql:/etc/postfix/sql-virtual_mailbox_maps.cf",
        "virtual_alias_maps": "mysql:/etc/postfix/sql-virtual_alias_maps.cf",
        "virtual_mailbox_domains": "mysql:/etc/postfix/sql-virtual_domains.cf",
        "transport_maps": "",
        "virtual_transport": "lmtp:unix:private/dovecot-lmtp",
        "recipient_delimiter": "+",
        "mlmmj_services": "",
        "dovecot.mail_uid": "5000",
        "dovecot.mail_gid": "5000",
        "dovecot.first_valid_uid": "5000",
        "dovecot.has_sql_passdb": "false",
        "dovecot.has_sql_userdb": "false",
        "dovecot.has_lmtp_listener": "false",
        "fs.base_uid": "5000",
        "fs.base_gid": "5000",
    }


def _env_for_settings() -> dict[str, str]:
    return {
        "POSTINO_IDENTITY_BACKEND": "local",
        "POSTINO_POSTFIX_SQL_DIR": "/etc/postfix",
        "POSTINO_VIRTUAL_MAILBOX_BASE": "/var/vmail",
        "POSTINO_POSTCREATION_HOOK": "/usr/local/bin/postcreation",
        "POSTINO_VMAIL_UID": "5000",
        "POSTINO_VMAIL_GID": "5000",
        "POSTINO_DEFAULT_PASSWORD_SCHEME": "BLF-CRYPT",
        "POSTINO_DEFAULT_QUOTA_BYTES": "1073741824",
        # Force wide + no-color so Rich doesn't wrap or ANSI-decorate the
        # output we assert against.
        "NO_COLOR": "1",
        "COLUMNS": "200",
        "TERM": "dumb",
    }


def test_diff_clean_exits_zero(runner: CliRunner) -> None:
    with patch("postino_core.config_gen.fix.detect", return_value=_detected_clean()):
        result = runner.invoke(app, ["config", "fix"], env=_env_for_settings())
    assert result.exit_code == 0, result.output
    assert "postconf -e" not in result.output


def test_detection_failure_exits_1(runner: CliRunner) -> None:
    from postino_core.errors import FixDetectionFailed

    with patch(
        "postino_core.config_gen.fix.detect",
        side_effect=FixDetectionFailed("postconf not on PATH"),
    ):
        result = runner.invoke(app, ["config", "fix"], env=_env_for_settings())
    assert result.exit_code == 1
    assert "postconf not on PATH" in result.output


def test_ambiguity_exits_2(runner: CliRunner) -> None:
    det = _detected_clean()
    det["dovecot.mail_uid"] = "1006"
    det["fs.base_uid"] = "5000"
    with patch("postino_core.config_gen.fix.detect", return_value=det):
        result = runner.invoke(app, ["config", "fix"], env=_env_for_settings())
    assert result.exit_code == 2
    assert "vmail" in result.output.lower()


def test_dovecot_conflict_exits_3_on_apply(runner: CliRunner) -> None:
    det = _detected_clean()
    det["dovecot.has_sql_passdb"] = "true"
    with patch("postino_core.config_gen.fix.detect", return_value=det):
        result = runner.invoke(
            app,
            ["config", "fix", "--apply", "--db-url", "mysql+pymysql://u:p@h/d"],
            env=_env_for_settings(),
        )
    assert result.exit_code == 3
