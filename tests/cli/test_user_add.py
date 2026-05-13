"""Tests for `postino user add --password-stdin`.

All three tests need a functioning ``_load_settings`` (which requires
``POSTINO_*`` env vars and the postfix sql-virtual_*.cf bundle). The
refusal tests never reach the DB — the helper raises before any SQL is
issued — but the CLI callback still must succeed, so we reuse the
integration env. Marker is module-level for consistency with the
sibling `test_user_cmd.py`."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from typer.testing import CliRunner

from postino.cli import app
from tests.cli.test_user_cmd import env_for_cli, make_postfix_cf, seed_domain

pytestmark = pytest.mark.integration

runner = CliRunner()


def _env(db_url: str, tmp_path: Path, hook: Path) -> dict[str, str]:
    sql_dir = tmp_path / "postfix"
    make_postfix_cf(db_url, sql_dir)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    return env_for_cli(db_url, mail_root, hook, sql_dir)


class _TTYBytesIO(io.BytesIO):
    """A BytesIO that reports itself as a TTY.

    Click's ``CliRunner`` swaps ``sys.stdin`` for whatever stream it
    derives from ``input=``; monkeypatching ``sys.stdin.isatty`` before
    invoke() therefore has no effect. Passing a binary stream whose
    ``isatty()`` returns True works around the swap deterministically.
    """

    def isatty(self) -> bool:
        return True


def test_user_add_password_stdin_rejects_tty(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """`--password-stdin` must refuse interactive stdin.

    Reading a password from a TTY without echo-suppression would leak
    keystrokes; --password-stdin is for pipes only."""
    del db  # fixture forces schema reset; not otherwise used.
    env = _env(os.environ["POSTINO_TEST_DB_URL"], tmp_path, fake_postcreation_hook)
    result = runner.invoke(
        app,
        ["user", "add", "x@example.com", "--password-stdin", "--quota", "1G"],
        env=env,
        input=_TTYBytesIO(b""),
    )
    assert result.exit_code == 4, result.output
    assert "interactive stdin" in result.stderr


def test_user_add_password_stdin_rejects_empty(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """Empty line on stdin is rejected before any DB write."""
    env = _env(os.environ["POSTINO_TEST_DB_URL"], tmp_path, fake_postcreation_hook)
    result = runner.invoke(
        app,
        ["user", "add", "x@example.com", "--password-stdin", "--quota", "1G"],
        env=env,
        input="\n",
    )
    assert result.exit_code == 4, result.output
    assert "empty password" in result.stderr


def test_user_add_reads_password_from_stdin(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """`--password-stdin` reads one line and provisions the mailbox.

    Verifies the happy path end-to-end: piped password creates a row
    visible to subsequent `user show`."""
    seed_domain(db, "example.com")
    env = _env(os.environ["POSTINO_TEST_DB_URL"], tmp_path, fake_postcreation_hook)

    result = runner.invoke(
        app,
        [
            "user",
            "add",
            "marketing@example.com",
            "--password-stdin",
            "--quota",
            "1G",
            "--name",
            "Marketing",
        ],
        env=env,
        input="hunter2\n",
    )
    assert result.exit_code == 0, result.output

    # Verify the row was created. Use --json to dodge rich-table column
    # truncation (which would render the address with an ellipsis).
    show = runner.invoke(app, ["--json", "user", "show", "marketing@example.com"], env=env)
    assert show.exit_code == 0, show.output
    assert "marketing@example.com" in show.output
