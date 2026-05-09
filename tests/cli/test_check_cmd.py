import os
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from typer.testing import CliRunner

from postino.cli import app
from tests.cli.test_user_cmd import env_for_cli, make_postfix_cf

pytestmark = pytest.mark.integration

runner = CliRunner()


def test_check_passes(db: Engine, tmp_path: Path, fake_postcreation_hook: Path) -> None:
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    sql_dir = tmp_path / "postfix"
    make_postfix_cf(db_url, sql_dir)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    env = env_for_cli(db_url, mail_root, fake_postcreation_hook, sql_dir)
    r = runner.invoke(app, ["check"], env=env)
    assert r.exit_code == 0, r.output
    assert "ok" in r.output.lower() or "✓" in r.output


def test_check_fails_when_hook_missing(
    db: Engine,
    tmp_path: Path,
) -> None:
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    sql_dir = tmp_path / "postfix"
    make_postfix_cf(db_url, sql_dir)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    env = env_for_cli(db_url, mail_root, tmp_path / "missing-hook.sh", sql_dir)
    r = runner.invoke(app, ["check"], env=env)
    assert r.exit_code != 0
