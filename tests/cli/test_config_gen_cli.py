"""CLI smoke for `postino config gen`.

These tests run the installed `postino` script via subprocess so they
exercise the real Typer wiring (entrypoint, callback skip-logic for
the `config` subgroup, exit codes). Marked `cli` so they run in the
non-integration phase — no DB required.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Same pattern as tests/e2e_cli/test_cli_e2e.py — invoke the installed
# console script via its absolute path so PATH-stripped subprocess envs
# can still find it.
_POSTINO_BIN = Path(sys.executable).parent / "postino"

# Strip ANSI escape sequences so substring assertions don't trip on
# rich/click colourised help output. Help also gets word-wrapped to
# the terminal width — force a wide COLUMNS so options like `--out`
# don't get hyphen-split across box borders on narrow CI shells.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _help_env() -> dict[str, str]:
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["COLUMNS"] = "200"
    env["TERM"] = "dumb"
    return env


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s).replace("\n", " ")


@pytest.mark.cli
def test_config_gen_help_exits_zero() -> None:
    """`postino config gen --help` must work with zero env / no DB."""
    result = subprocess.run(
        [str(_POSTINO_BIN), "config", "gen", "--help"],
        capture_output=True,
        text=True,
        env=_help_env(),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    out = _strip_ansi(result.stdout)
    assert "--out" in out
    assert "--identity-backend" in out


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
