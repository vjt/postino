"""Subcommand `--help` epilog footers must point at global flags.

Operators discovering `postino user --help` should know that --json,
--quiet, and --no-color exist at the global level (parsed by the root
callback). Without the epilog, they'd assume each subcommand has its
own JSON flag and get confused. The footer text is identical across all
sub-Typer groups so it's predictable.

Integration-marked because the root callback runs `_load_settings()`
before typer/click reaches the subcommand-level eager `--help` parsing,
so the DB + postfix scaffold env is required even to render --help
output. Same pattern as test_domain_alias_cmd.py.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from typer.testing import CliRunner

from postino.cli import app
from tests.cli.test_user_cmd import env_for_cli, make_postfix_cf

pytestmark = pytest.mark.integration

runner = CliRunner()


def _bootstrap(db_url: str, tmp_path: Path, hook: Path) -> dict[str, str]:
    sql_dir = tmp_path / "postfix"
    make_postfix_cf(db_url, sql_dir)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    return env_for_cli(db_url, mail_root, hook, sql_dir)


@pytest.mark.parametrize(
    "subcommand",
    ["user", "alias", "domain", "list", "check", "status", "quota"],
)
def test_subcommand_help_has_epilog(
    subcommand: str,
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """Each subcommand --help should mention the global flags."""
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    env = _bootstrap(db_url, tmp_path, fake_postcreation_hook)
    result = runner.invoke(app, [subcommand, "--help"], env=env)
    assert result.exit_code == 0, result.output
    assert "--json" in result.output, (
        f"{subcommand} --help missing --json reference; got:\n{result.output}"
    )
    assert "global options" in result.output.lower() or "postino --help" in result.output, (
        f"{subcommand} --help missing global-flags pointer; got:\n{result.output}"
    )


def test_domain_alias_help_has_epilog(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """`postino domain alias --help` is a nested sub-Typer; same treatment."""
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    env = _bootstrap(db_url, tmp_path, fake_postcreation_hook)
    result = runner.invoke(app, ["domain", "alias", "--help"], env=env)
    assert result.exit_code == 0, result.output
    assert "--json" in result.output
    assert "global options" in result.output.lower() or "postino --help" in result.output
