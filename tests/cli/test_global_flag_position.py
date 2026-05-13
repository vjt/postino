"""End-to-end tests: global flags accepted at any argv position.

Production wires ``--json``/``--quiet``/``--no-color`` through Typer's
root callback, which natively only accepts them BEFORE the subcommand.
``postino.__main__.main`` shuffles ``sys.argv`` so operators can write
them anywhere. The shuffle is the contract.

CliRunner.invoke bypasses ``sys.argv`` (it passes args directly to
``cli.main(args=...)``), so we mirror the production shuffle here via
the ``_invoke`` helper. This tests the same code path the installed
``postino`` console script uses.

All tests are integration-marked: they hit ``domain list`` / ``user
add`` which need a seeded DB.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from click.testing import Result
from sqlalchemy.engine import Engine
from typer.testing import CliRunner

from postino.__main__ import (
    _FLOATING_GLOBALS,  # pyright: ignore[reportPrivateUsage]  # WHY: module-private allow-list reused so the test shuffle mirrors production exactly.
    _shuffle_globals,  # pyright: ignore[reportPrivateUsage]  # WHY: module-private helper reused so the test shuffle mirrors production exactly.
)
from postino.cli import app
from tests.cli.test_user_cmd import env_for_cli, make_postfix_cf, seed_domain

pytestmark = pytest.mark.integration

runner = CliRunner()


def _invoke(rnr: CliRunner, args: list[str], **kwargs: Any) -> Result:
    """Invoke ``app`` with the same argv shuffling production performs.

    CliRunner.invoke does not consult ``sys.argv``, so the
    ``__main__.main`` shuffle is bypassed in tests. This helper mirrors
    that shuffle on the args list so the test exercises the contract
    operators actually see when running the installed binary.
    """
    return rnr.invoke(app, _shuffle_globals(args, _FLOATING_GLOBALS), **kwargs)


def _env(tmp_path: Path, fake_postcreation_hook: Path) -> dict[str, str]:
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    sql_dir = tmp_path / "postfix"
    make_postfix_cf(db_url, sql_dir)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    env = env_for_cli(db_url, mail_root, fake_postcreation_hook, sql_dir)
    # Wide "terminal" so example.com doesn't get truncated in the table.
    env["COLUMNS"] = "400"
    return env


def test_json_works_at_end_of_argv(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """``--json`` at the end produces the same output as ``--json`` at the start."""
    seed_domain(db, "example.com")
    env = _env(tmp_path, fake_postcreation_hook)

    leading = _invoke(runner, ["--json", "domain", "list"], env=env)
    trailing = _invoke(runner, ["domain", "list", "--json"], env=env)
    assert leading.exit_code == 0, leading.output
    assert trailing.exit_code == 0, trailing.output
    assert leading.stdout == trailing.stdout


def test_json_works_in_middle_of_argv(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """``--json`` between subcommand and sub-subcommand is also accepted."""
    seed_domain(db, "example.com")
    env = _env(tmp_path, fake_postcreation_hook)

    middle = _invoke(runner, ["domain", "--json", "list"], env=env)
    leading = _invoke(runner, ["--json", "domain", "list"], env=env)
    assert middle.exit_code == 0, middle.output
    assert leading.exit_code == 0, leading.output
    assert middle.stdout == leading.stdout


def test_quiet_no_color_also_shuffle(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """``--quiet`` and ``--no-color`` are also accepted at the tail.

    The ANSI absence assertion is partially vacuous under CliRunner:
    Rich auto-detects non-tty output and emits no ANSI regardless
    (as documented in test_global_flags.py). We still assert it as a
    belt-and-braces check; the load-bearing assertion here is the
    zero exit code (the flags were accepted, not rejected as unknown).
    """
    seed_domain(db, "example.com")
    env = _env(tmp_path, fake_postcreation_hook)

    result = _invoke(runner, ["domain", "list", "--quiet", "--no-color"], env=env)
    assert result.exit_code == 0, result.output
    assert "\x1b[" not in result.stdout


def test_non_global_flags_stay_put(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """Subcommand-local flags (``--quota``, ``--password-stdin``) are not shuffled.

    The shuffle is allow-list-only: only the three floating globals
    move. Subcommand-bound flags retain their position so Typer can
    bind them on the subcommand level as declared.
    """
    seed_domain(db, "example.com")
    env = _env(tmp_path, fake_postcreation_hook)

    result = _invoke(
        runner,
        [
            "user",
            "add",
            "x@example.com",
            "--quota",
            "1G",
            "--password-stdin",
            "--json",
        ],
        env=env,
        input="hunter2\n",
    )
    assert result.exit_code == 0, result.output
