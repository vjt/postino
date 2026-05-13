"""CLI integration test for the config-error file:line surface.

End-to-end check: write a TOML with a bad value, point POSTINO_CONFIG
at it, run ``postino check``, assert the CLI exits 4 (ConfigError) and
the stderr message names the file + line + offending field.

This file does NOT carry ``pytestmark = pytest.mark.integration`` —
the test fails before any service-build step, so it never touches the
database. The integration directory hosts both DB-bound and CLI-only
tests (see test_settings_precedence.py which is also unmarked at the
module level)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from postino.cli import app


def test_cli_reports_file_line_on_bad_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Scrub leaking POSTINO_* env vars: the runner inherits the parent
    # process env, and any leftover POSTINO_DEFAULT_QUOTA_BYTES would
    # mask the bad-TOML value and pass validation.
    for key in list(os.environ):
        if key.startswith("POSTINO_"):
            monkeypatch.delenv(key, raising=False)

    toml = tmp_path / "postino.toml"
    toml.write_text(
        'identity_backend = "local"\n'
        'postfix_sql_dir = "/usr/local/etc/postfix"\n'
        'virtual_mailbox_base = "/srv/mail"\n'
        'postcreation_hook = "/bin/true"\n'
        "vmail_uid = 1006\n"
        "vmail_gid = 1006\n"
        'default_quota_bytes = "1gb"\n'
    )
    monkeypatch.setenv("POSTINO_CONFIG", str(toml))

    runner = CliRunner()
    result = runner.invoke(app, ["check"])

    assert result.exit_code == 4
    assert f"{toml}:7" in result.stderr
    assert "default_quota_bytes" in result.stderr
