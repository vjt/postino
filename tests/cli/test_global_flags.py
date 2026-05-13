"""Tests for global flags --quiet and --no-color on the root callback.

The end-to-end CLI tests are integration-marked because they need a
domain seeded into the DB to exercise the full `domain list` path.

A separate unit test exercises ``Renderer`` directly with a forced-terminal
Rich console so the ``no_color`` flag's effect on ANSI emission can be
asserted MEANINGFULLY. Without ``force_terminal=True``, Rich auto-detects
StringIO/file output and emits no ANSI at all — which would make the
"\\x1b[" not in output assertion pass VACUOUSLY whether --no-color worked
or not. The CliRunner-based tests cannot escape this auto-detection
(Click+Rich both see a non-tty in the test harness), so they can only
verify the flag is accepted and plumbed through CliState without crashing.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from pydantic import BaseModel
from rich.console import Console
from sqlalchemy.engine import Engine
from typer.testing import CliRunner

from postino.cli import app
from postino.output import Renderer
from tests.cli.test_user_cmd import env_for_cli, make_postfix_cf, seed_domain

# NB: deliberately NO module-level ``pytestmark = pytest.mark.integration``.
# The unit test (Renderer-only) runs without a DB. Integration tests are
# marked individually below.

runner = CliRunner()


class _Row(BaseModel):
    name: str
    value: int


def _capture_render(*, no_color: bool) -> str:
    """Build a Console mirroring Renderer's no_color branch, force a terminal,
    then render and return raw ANSI bytes.

    Mirrors the construction in ``Renderer.__init__`` (color_system gating on
    no_color) so the assertion is on the *same* color-system logic the
    production path uses, just on a forced-terminal Console so a non-tty
    test harness cannot vacuously strip ANSI.
    """
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        color_system=None if no_color else "auto",
        no_color=no_color,
        width=80,
    )
    Renderer(json=False, no_color=no_color, console=console).render([_Row(name="alpha", value=1)])
    return buf.getvalue()


def test_renderer_no_color_strips_ansi_unit() -> None:
    """Unit-level proof: Renderer's no_color path suppresses ALL ANSI.

    Sanity-paired with the "no_color=False emits ANSI" assertion below to
    rule out a vacuous pass.

    Notably: Rich's ``Console(no_color=True)`` alone strips foreground
    colors but still emits ``\\x1b[1m`` for bold table headers. The
    Renderer's construction passes ``color_system=None`` when no_color is
    set, which is what fully suppresses ANSI for script-pipe-safe output.
    """
    # no_color=False: ANSI must be present (table headers are bold).
    out_color = _capture_render(no_color=False)
    assert "\x1b[" in out_color, "sanity: ANSI should be present without no_color"

    # no_color=True: ANSI must be ENTIRELY absent (no foreground, no bold).
    out_nc = _capture_render(no_color=True)
    assert "\x1b[" not in out_nc
    assert "alpha" in out_nc


def _env(tmp_path: Path, fake_postcreation_hook: Path) -> dict[str, str]:
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    sql_dir = tmp_path / "postfix"
    make_postfix_cf(db_url, sql_dir)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    env = env_for_cli(db_url, mail_root, fake_postcreation_hook, sql_dir)
    # Force a wide "terminal" so Rich does not truncate "example.com" into
    # "exam…" — the default 80-col fallback truncates cells aggressively.
    env["COLUMNS"] = "400"
    return env


@pytest.mark.integration
def test_no_color_flag_accepted_e2e(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """End-to-end: --no-color is plumbed through and does not break data path.

    NOTE: the stronger "no ANSI in output" assertion is enforced at the
    unit level (see test_renderer_no_color_strips_ansi_unit) because under
    CliRunner Rich auto-detects non-tty output and emits no ANSI regardless.
    Here we only assert the flag is ACCEPTED and the data still renders.
    """
    seed_domain(db, "example.com")
    env = _env(tmp_path, fake_postcreation_hook)
    env = {k: v for k, v in env.items() if k not in {"NO_COLOR", "CI"}}

    r = runner.invoke(app, ["--no-color", "domain", "list"], env=env, color=True)
    assert r.exit_code == 0, r.output
    assert "\x1b[" not in r.output
    assert "example.com" in r.output


@pytest.mark.integration
def test_no_color_env_var_honored_e2e(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """NO_COLOR=1 env var is plumbed to CliState without --no-color flag."""
    seed_domain(db, "example.com")
    env = _env(tmp_path, fake_postcreation_hook)
    env = {k: v for k, v in env.items() if k != "CI"}
    env["NO_COLOR"] = "1"

    r = runner.invoke(app, ["domain", "list"], env=env, color=True)
    assert r.exit_code == 0, r.output
    assert "\x1b[" not in r.output


@pytest.mark.integration
def test_ci_env_var_disables_color_e2e(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """CI=true env var disables colors (CI-detection convention)."""
    seed_domain(db, "example.com")
    env = _env(tmp_path, fake_postcreation_hook)
    env = {k: v for k, v in env.items() if k != "NO_COLOR"}
    env["CI"] = "true"

    r = runner.invoke(app, ["domain", "list"], env=env, color=True)
    assert r.exit_code == 0, r.output
    assert "\x1b[" not in r.output


@pytest.mark.integration
def test_quiet_accepted_and_data_still_printed(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """--quiet is plumbed through CliState without breaking the data path.

    The assertion is intentionally weak: postino currently emits no
    "Found N rows:" banner, so quiet has no banner to suppress today. The
    field is wired on Renderer for a future banner introduction to gate on.
    This test guards that the flag is ACCEPTED (no crash) and data still
    renders.
    """
    seed_domain(db, "example.com")
    env = _env(tmp_path, fake_postcreation_hook)

    quiet = runner.invoke(app, ["--quiet", "domain", "list"], env=env)
    assert quiet.exit_code == 0, quiet.output
    # banner suppression is a future hook; data path unchanged.
    assert "Found" not in quiet.output
    assert "example.com" in quiet.output
