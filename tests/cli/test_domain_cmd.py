import os
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from typer.testing import CliRunner

from postino.cli import app
from tests.cli.test_user_cmd import env_for_cli, make_postfix_cf

pytestmark = pytest.mark.integration

runner = CliRunner()


def test_domain_add_list_del(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    sql_dir = tmp_path / "postfix"
    make_postfix_cf(db_url, sql_dir)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    env = env_for_cli(db_url, mail_root, fake_postcreation_hook, sql_dir)

    r = runner.invoke(
        app,
        [
            "domain",
            "add",
            "x.example.org",
            "--description",
            "test",
            "--max-mailboxes",
            "5",
            "--default-quota",
            "1G",
            "--transport",
            "virtual",
        ],
        env=env,
    )
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["--json", "domain", "list"], env=env)
    assert r.exit_code == 0
    assert "x.example.org" in r.output

    r = runner.invoke(app, ["domain", "del", "x.example.org", "--yes"], env=env)
    assert r.exit_code == 0


def test_domain_disable_then_enable(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """`disable` then `enable` round-trips domain.active."""
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    sql_dir = tmp_path / "postfix"
    make_postfix_cf(db_url, sql_dir)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    env = env_for_cli(db_url, mail_root, fake_postcreation_hook, sql_dir)

    r = runner.invoke(
        app,
        ["domain", "add", "example.it", "--description", "test", "--transport", "virtual"],
        env=env,
    )
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["domain", "disable", "example.it"], env=env)
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["domain", "enable", "example.it"], env=env)
    assert r.exit_code == 0, r.output

    # Cleanup so the test is idempotent against a persistent DB.
    r = runner.invoke(app, ["domain", "del", "example.it", "--yes"], env=env)
    assert r.exit_code == 0, r.output


def test_domain_enable_missing_exits_1(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """NotFoundError → exit code 1 for unknown domain."""
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    sql_dir = tmp_path / "postfix"
    make_postfix_cf(db_url, sql_dir)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    env = env_for_cli(db_url, mail_root, fake_postcreation_hook, sql_dir)

    r = runner.invoke(app, ["domain", "enable", "ghost.example.org"], env=env)
    assert r.exit_code == 1, r.output
