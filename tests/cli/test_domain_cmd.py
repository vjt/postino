import os
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from typer.testing import CliRunner

from postino.cli import app
from tests.cli.test_user_cmd import _env, _make_postfix_cf

pytestmark = pytest.mark.integration

runner = CliRunner()


def test_domain_add_list_del(
    db: Engine, tmp_path: Path, fake_postcreation_hook: Path,
) -> None:
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    sql_dir = tmp_path / "postfix"
    _make_postfix_cf(db_url, sql_dir)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    env = _env(db_url, mail_root, fake_postcreation_hook, sql_dir)

    r = runner.invoke(app, [
        "domain", "add", "x.test",
        "--description", "test",
        "--max-mailboxes", "5",
        "--default-quota", "1G",
        "--transport", "virtual",
    ], env=env)
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["domain", "list", "--json"], env=env)
    assert r.exit_code == 0
    assert "x.test" in r.output

    r = runner.invoke(app, ["domain", "del", "x.test", "--yes"], env=env)
    assert r.exit_code == 0
