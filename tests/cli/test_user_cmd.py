import os
from pathlib import Path

import pytest
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine
from typer.testing import CliRunner

from postino.cli import app

pytestmark = pytest.mark.integration

runner = CliRunner()


def seed_domain(db: Engine, domain: str) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        conn.execute(
            md.tables["domain"]
            .insert()
            .values(
                domain=domain,
                description="",
                aliases=0,
                mailboxes=10,
                maxquota=0,
                quota=0,
                transport="virtual",
                backupmx=0,
                active=1,
            )
        )


def env_for_cli(db_url: str, mail_root: Path, hook: Path, sql_dir: Path) -> dict[str, str]:
    return {
        **os.environ,
        "POSTINO_IDENTITY_BACKEND": "local",
        "POSTINO_POSTFIX_SQL_DIR": str(sql_dir),
        "POSTINO_VIRTUAL_MAILBOX_BASE": str(mail_root),
        "POSTINO_POSTCREATION_HOOK": str(hook),
        "POSTINO_VMAIL_UID": "-1",
        "POSTINO_VMAIL_GID": "-1",
        "POSTINO_DEFAULT_PASSWORD_SCHEME": "BLF-CRYPT",
        "POSTINO_DEFAULT_QUOTA_BYTES": "1073741824",
        "POSTINO_DB_URL_OVERRIDE": db_url,
    }


def make_postfix_cf(db_url: str, sql_dir: Path) -> None:
    body = db_url.replace("mysql+pymysql://", "")
    auth, _, hostdb = body.partition("@")
    user, _, pwd = auth.partition(":")
    host, _, dbname = hostdb.partition("/")
    sql_dir.mkdir(exist_ok=True)
    (sql_dir / "sql-virtual_mailbox_maps.cf").write_text(
        f"hosts = {host}\nuser = {user}\npassword = {pwd}\ndbname = {dbname}\n"
    )


def test_user_add_then_list(
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

    result = runner.invoke(
        app,
        [
            "user",
            "add",
            "foo@example.com",
            "--password",
            "hunter2",
            "--name",
            "Foo",
            "--quota",
            "5G",
        ],
        env=env,
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["--json", "user", "list"], env=env)
    assert result.exit_code == 0
    assert "foo@example.com" in result.output


def test_user_add_unknown_domain_exit_1(
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

    result = runner.invoke(
        app,
        [
            "user",
            "add",
            "x@noexist.test",
            "--password",
            "p",
            "--name",
            "",
            "--quota",
            "0",
        ],
        env=env,
    )
    assert result.exit_code == 1, result.output
