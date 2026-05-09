import os
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from typer.testing import CliRunner

from postino.cli import app
from tests.cli.test_user_cmd import env_for_cli, make_postfix_cf, seed_domain

pytestmark = pytest.mark.integration

runner = CliRunner()


def test_alias_add_list(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    seed_domain(db, "example.com")
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    sql_dir = tmp_path / "postfix"
    make_postfix_cf(db_url, sql_dir)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    env = env_for_cli(db_url, mail_root, fake_postcreation_hook, sql_dir)

    r = runner.invoke(app, ["alias", "add", "foo@example.com", "bar@example.com"], env=env)
    assert r.exit_code == 0

    r = runner.invoke(app, ["alias", "list", "--json"], env=env)
    assert r.exit_code == 0
    assert "foo@example.com" in r.output
