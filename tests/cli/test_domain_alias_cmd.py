"""CLI tests for ``postino domain alias …`` nested subcommand.

Covers add / list / show / del + enable / disable / retarget verbs
wired in commands/domain.py."""

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


def _bootstrap(
    db_url: str,
    tmp_path: Path,
    hook: Path,
) -> dict[str, str]:
    """Common env+postfix scaffolding for every test in this module."""
    sql_dir = tmp_path / "postfix"
    make_postfix_cf(db_url, sql_dir)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    return env_for_cli(db_url, mail_root, hook, sql_dir)


def _seed_domain(env: dict[str, str], name: str) -> None:
    r = runner.invoke(
        app,
        [
            "domain",
            "add",
            name,
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


def test_help_lists_alias_subcommand(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """`postino domain --help` must surface the `alias` sub-typer."""
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    env = _bootstrap(db_url, tmp_path, fake_postcreation_hook)
    r = runner.invoke(app, ["domain", "--help"], env=env)
    assert r.exit_code == 0, r.output
    assert "alias" in r.output

    r = runner.invoke(app, ["domain", "alias", "--help"], env=env)
    assert r.exit_code == 0, r.output
    for verb in ("add", "list", "show", "del"):
        assert verb in r.output, r.output


def test_alias_add_happy_path(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    env = _bootstrap(db_url, tmp_path, fake_postcreation_hook)
    _seed_domain(env, "src.example.org")
    _seed_domain(env, "tgt.example.org")

    r = runner.invoke(
        app,
        [
            "--json",
            "domain",
            "alias",
            "add",
            "src.example.org",
            "--target",
            "tgt.example.org",
        ],
        env=env,
    )
    assert r.exit_code == 0, r.output
    assert "src.example.org" in r.output
    assert "tgt.example.org" in r.output


def test_alias_add_self_alias_returns_exit_10(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """RuleViolationError → exit code 10."""
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    env = _bootstrap(db_url, tmp_path, fake_postcreation_hook)
    _seed_domain(env, "loop.example.org")

    r = runner.invoke(
        app,
        [
            "domain",
            "alias",
            "add",
            "loop.example.org",
            "--target",
            "loop.example.org",
        ],
        env=env,
    )
    assert r.exit_code == 10, r.output


def test_alias_list_json_returns_row(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    env = _bootstrap(db_url, tmp_path, fake_postcreation_hook)
    _seed_domain(env, "src.example.org")
    _seed_domain(env, "tgt.example.org")
    r = runner.invoke(
        app,
        [
            "domain",
            "alias",
            "add",
            "src.example.org",
            "--target",
            "tgt.example.org",
        ],
        env=env,
    )
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["--json", "domain", "alias", "list"], env=env)
    assert r.exit_code == 0, r.output
    assert "src.example.org" in r.output
    assert "tgt.example.org" in r.output


def test_alias_show_missing_returns_exit_1(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """NotFoundError → exit code 1."""
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    env = _bootstrap(db_url, tmp_path, fake_postcreation_hook)
    r = runner.invoke(
        app,
        ["domain", "alias", "show", "nope.example.org"],
        env=env,
    )
    assert r.exit_code == 1, r.output


def test_alias_del_with_yes(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    env = _bootstrap(db_url, tmp_path, fake_postcreation_hook)
    _seed_domain(env, "src.example.org")
    _seed_domain(env, "tgt.example.org")
    r = runner.invoke(
        app,
        [
            "domain",
            "alias",
            "add",
            "src.example.org",
            "--target",
            "tgt.example.org",
        ],
        env=env,
    )
    assert r.exit_code == 0, r.output

    r = runner.invoke(
        app,
        ["domain", "alias", "del", "src.example.org", "--yes"],
        env=env,
    )
    assert r.exit_code == 0, r.output

    # Verify gone: show now 404s.
    r = runner.invoke(
        app,
        ["domain", "alias", "show", "src.example.org"],
        env=env,
    )
    assert r.exit_code == 1, r.output


def test_disable_then_enable_round_trip(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """`disable` then `enable` round-trips alias_domain.active and the
    default `alias list` (enabled-only) reflects each state."""
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    env = _bootstrap(db_url, tmp_path, fake_postcreation_hook)
    _seed_domain(env, "src.example.org")
    _seed_domain(env, "tgt.example.org")

    r = runner.invoke(
        app,
        [
            "domain",
            "alias",
            "add",
            "src.example.org",
            "--target",
            "tgt.example.org",
        ],
        env=env,
    )
    assert r.exit_code == 0, r.output

    # Disable
    r = runner.invoke(
        app,
        ["domain", "alias", "disable", "src.example.org"],
        env=env,
    )
    assert r.exit_code == 0, r.output

    # Default list filters disabled → empty
    r = runner.invoke(app, ["--json", "domain", "alias", "list"], env=env)
    assert r.exit_code == 0, r.output
    assert "src.example.org" not in r.output

    # --all surfaces the disabled row
    r = runner.invoke(app, ["--json", "domain", "alias", "list", "--all"], env=env)
    assert r.exit_code == 0, r.output
    assert "src.example.org" in r.output

    # Re-enable
    r = runner.invoke(
        app,
        ["domain", "alias", "enable", "src.example.org"],
        env=env,
    )
    assert r.exit_code == 0, r.output

    # Default list now shows the row again
    r = runner.invoke(app, ["--json", "domain", "alias", "list"], env=env)
    assert r.exit_code == 0, r.output
    assert "src.example.org" in r.output


def test_disable_missing_returns_exit_1(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """NotFoundError → exit code 1 for unknown alias_domain."""
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    env = _bootstrap(db_url, tmp_path, fake_postcreation_hook)

    r = runner.invoke(
        app,
        ["domain", "alias", "disable", "nope.example.org"],
        env=env,
    )
    assert r.exit_code == 1, r.output


def test_retarget_happy(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """`retarget` repoints alias_domain to a fresh target domain."""
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    env = _bootstrap(db_url, tmp_path, fake_postcreation_hook)
    _seed_domain(env, "src.example.org")
    _seed_domain(env, "tgt1.example.org")
    _seed_domain(env, "tgt2.example.org")

    r = runner.invoke(
        app,
        [
            "domain",
            "alias",
            "add",
            "src.example.org",
            "--target",
            "tgt1.example.org",
        ],
        env=env,
    )
    assert r.exit_code == 0, r.output

    r = runner.invoke(
        app,
        [
            "domain",
            "alias",
            "retarget",
            "src.example.org",
            "--target",
            "tgt2.example.org",
        ],
        env=env,
    )
    assert r.exit_code == 0, r.output

    # Confirm the new target via JSON list.
    r = runner.invoke(app, ["--json", "domain", "alias", "list"], env=env)
    assert r.exit_code == 0, r.output
    assert "tgt2.example.org" in r.output
    assert "tgt1.example.org" not in r.output


def test_retarget_missing_target_returns_exit_1(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """Retargeting to an unknown domain → exit code 1 (NotFoundError)."""
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    env = _bootstrap(db_url, tmp_path, fake_postcreation_hook)
    _seed_domain(env, "src.example.org")
    _seed_domain(env, "tgt.example.org")

    r = runner.invoke(
        app,
        [
            "domain",
            "alias",
            "add",
            "src.example.org",
            "--target",
            "tgt.example.org",
        ],
        env=env,
    )
    assert r.exit_code == 0, r.output

    r = runner.invoke(
        app,
        [
            "domain",
            "alias",
            "retarget",
            "src.example.org",
            "--target",
            "ghost.example.org",
        ],
        env=env,
    )
    assert r.exit_code == 1, r.output
