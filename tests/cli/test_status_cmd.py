"""End-to-end `postino status` and `postino reconcile` CLI tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from typer.testing import CliRunner

from postino.cli import app
from tests.cli.test_user_cmd import env_for_cli, make_postfix_cf

pytestmark = pytest.mark.integration

runner = CliRunner()


def test_status_human_renders_a_table(
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
    r = runner.invoke(app, ["status"], env=env)
    assert r.exit_code == 0, r.output
    # Pydantic field names from StatusReport are the table header.
    assert "domains" in r.output
    assert "mailboxes" in r.output


def test_status_json_emits_status_report(
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
    r = runner.invoke(app, ["--json", "status"], env=env)
    assert r.exit_code == 0, r.output
    payload = json.loads(r.stdout)
    assert set(payload.keys()) == {"domains", "mailboxes", "aliases", "quota2"}
    for v in payload.values():
        assert isinstance(v, int)


def test_reconcile_stub_exits_4(
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
    r = runner.invoke(app, ["reconcile"], env=env)
    assert r.exit_code == 4
    assert "reconcile" in r.output.lower()
