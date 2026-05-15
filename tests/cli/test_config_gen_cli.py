"""CLI smoke for `postino config gen`.

These tests run the installed `postino` script via subprocess so they
exercise the real Typer wiring (entrypoint, callback skip-logic for
the `config` subgroup, exit codes). Marked `cli` so they run in the
non-integration phase — no DB required.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Same pattern as tests/e2e_cli/test_cli_e2e.py — invoke the installed
# console script via its absolute path so PATH-stripped subprocess envs
# can still find it.
_POSTINO_BIN = Path(sys.executable).parent / "postino"


@pytest.mark.cli
def test_config_gen_help_exits_zero() -> None:
    """`postino config gen --help` must work with zero env / no DB."""
    result = subprocess.run(
        [str(_POSTINO_BIN), "config", "gen", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--out" in result.stdout
    assert "--identity-backend" in result.stdout


@pytest.mark.cli
def test_config_gen_rejects_no_creds_when_not_tty() -> None:
    """No --db-url, no env var, non-TTY stdin → exit non-zero, no traceback."""
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin"}
    result = subprocess.run(
        [str(_POSTINO_BIN), "config", "gen", "--identity-backend", "local"],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    # Either typer's BadParameter (exit 2 from Click usage error) or our
    # in-handler exit 1; the assertion is "non-zero and our message
    # surfaced", not the exact code — Click's BadParameter handling
    # short-circuits before our try/except.
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "TTY" in combined or "POSTINO_DB_URL" in combined or "db-url" in combined


@pytest.mark.cli
def test_config_gen_rejects_unknown_only_name() -> None:
    """`--only <bogus>` must fail before any DB lookup happens."""
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "POSTINO_DB_URL": "mysql://x:y@z/w",
    }
    result = subprocess.run(
        [
            str(_POSTINO_BIN),
            "config",
            "gen",
            "--identity-backend",
            "local",
            "--only",
            "nonexistent_renderer",
        ],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert result.returncode != 0
    assert "nonexistent_renderer" in (result.stderr + result.stdout)
