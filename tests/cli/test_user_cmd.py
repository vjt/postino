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
    """Build the env that drives `postino` against the test DB.

    The ``db_url`` argument is honored implicitly: ``make_postfix_cf``
    writes the test creds into the postfix sql-virtual_*.cf files at
    ``sql_dir`` and ``POSTINO_POSTFIX_SQL_DIR`` points the CLI there.
    No back-channel env var exists in production code paths (PR-A6 ripped
    out ``POSTINO_DB_URL_OVERRIDE`` — postfix is the single source of
    truth for SQL credentials)."""
    del db_url  # routed through make_postfix_cf → sql_dir; kept for caller-API symmetry.
    return {
        **os.environ,
        "POSTINO_IDENTITY_BACKEND": "local",
        "POSTINO_POSTFIX_SQL_DIR": str(sql_dir),
        "POSTINO_VIRTUAL_MAILBOX_BASE": str(mail_root),
        "POSTINO_POSTCREATION_HOOK": str(hook),
        # Use the running user's uid/gid so v0.11's _check_vmail_identity
        # resolves them (warn for non-vmail name, but no error → exit 0).
        # Sentinel -1 used to be fine before v0.11; the new check errors
        # on uid/gid that don't resolve via pwd.getpwuid/grp.getgrgid.
        "POSTINO_VMAIL_UID": str(os.getuid()),
        "POSTINO_VMAIL_GID": str(os.getgid()),
        "POSTINO_DEFAULT_PASSWORD_SCHEME": "BLF-CRYPT",
        "POSTINO_DEFAULT_QUOTA_BYTES": "1073741824",
    }


def make_postfix_cf(db_url: str, sql_dir: Path, *, include_alias_domain: bool = False) -> None:
    body = db_url.replace("mysql+pymysql://", "")
    auth, _, hostdb = body.partition("@")
    user, _, pwd = auth.partition(":")
    host, _, dbname = hostdb.partition("/")
    # Postfix sql-virtual_*.cf `hosts = ...` field expects a bare hostname.
    # Strip any `:port` suffix from URL forms like `127.0.0.1:3306` — the
    # port is implicit MySQL 3306 in cf semantics. Without this, SQLAlchemy
    # URL.create stuffs `host:port` into the hostname and PyMySQL fails
    # getaddrinfo with EAI_NONAME ("Name or service not known"). CI hit
    # this because GH Actions services use 127.0.0.1:3306 in the URL;
    # local .env uses bare `localhost` so the bug stayed hidden.
    host, _, _port = host.partition(":")
    sql_dir.mkdir(exist_ok=True)
    cf_body = f"hosts = {host}\nuser = {user}\npassword = {pwd}\ndbname = {dbname}\n"
    # All three core cf files written so `postino check` (which now verifies
    # each one matches the engine URL) passes against the test bundle.
    # ``postino check`` also asserts the cf-file is not world/group-readable
    # (the file embeds the SQL password); chmod 0o600 so tests pass
    # regardless of the operator's umask.
    files = [
        "sql-virtual_mailbox_maps.cf",
        "sql-virtual_alias_maps.cf",
        "sql-virtual_domains.cf",
    ]
    if include_alias_domain:
        # The 2 conditional cf files — required by `postino check` only
        # when the ``alias_domain`` table has rows. Opt-in to keep
        # existing callers (whose tests don't seed alias_domain) green.
        files.extend(
            [
                "sql-virtual_alias_alias_domain_maps.cf",
                "sql-virtual_mailbox_alias_domain_maps.cf",
            ]
        )
    for filename in files:
        cf_path = sql_dir / filename
        cf_path.write_text(cf_body)
        cf_path.chmod(0o600)


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
            "--name",
            "Foo",
            "--quota",
            "5G",
        ],
        env=env,
        input="hunter2\nhunter2\n",
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
            "x@noexist.example.org",
            "--name",
            "",
            "--quota",
            "0",
        ],
        env=env,
        input="p\np\n",
    )
    assert result.exit_code == 1, result.output


def test_user_add_rejects_password_on_argv(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """`--password` and `-p` must not exist on the CLI surface.

    Passwords on argv leak via `ps`, shell history, syslog audit, and
    CI logs. Force the prompt path."""
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    sql_dir = tmp_path / "postfix"
    make_postfix_cf(db_url, sql_dir)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    env = env_for_cli(db_url, mail_root, fake_postcreation_hook, sql_dir)

    for argv in (
        ["user", "add", "x@example.com", "--password", "p"],
        ["user", "passwd", "x@example.com", "--password", "p"],
    ):
        result = runner.invoke(app, argv, env=env)
        assert result.exit_code != 0, f"{argv} unexpectedly accepted: {result.output}"
        assert "--password" not in result.output or "no such option" in result.output.lower()
