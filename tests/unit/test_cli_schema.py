"""Unit tests for `postino schema` subcommand surface.

Only the --help output is exercised; no DB or settings file needed.
The root CLI callback is bypassed via the same mock_services pattern
used throughout the CLI unit suite.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from postino import cli as cli_module
from postino.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def mock_services(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Bypass _load_settings + build_services so --help works without a DB."""
    bundle = MagicMock(name="ServicesBundle")

    def _fake_load_settings() -> object:
        return object()

    def _fake_build_services(*_a: Any, **_kw: Any) -> MagicMock:
        return bundle

    monkeypatch.setattr(cli_module, "_load_settings", _fake_load_settings)
    monkeypatch.setattr(cli_module, "build_services", _fake_build_services)
    return bundle


def test_schema_migrate_help_exits_zero(runner: CliRunner, mock_services: MagicMock) -> None:
    """postino schema migrate --help returns exit 0."""
    result = runner.invoke(app, ["schema", "migrate", "--help"])
    assert result.exit_code == 0, f"expected 0, got {result.exit_code}:\n{result.output}"


def test_schema_migrate_help_mentions_routes_and_version(
    runner: CliRunner, mock_services: MagicMock
) -> None:
    """Help text references both 'routes' and 'v0.10' so operators know what it does."""
    result = runner.invoke(app, ["schema", "migrate", "--help"])
    output = result.output
    assert "routes" in output, f"'routes' not found in help output:\n{output}"
    assert "v0.10" in output, f"'v0.10' not found in help output:\n{output}"


def test_entry_skips_build_services_for_schema_subcommand(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v0.10.2 regression: invoking ``postino schema *`` must NOT call
    ``build_services`` from the root ``_entry`` callback.

    Pre-fix, ``_entry`` unconditionally called
    ``build_services(settings)`` → ``reflect_schema(only=_REQUIRED_TABLES)``,
    which crashed with::

        InvalidRequestError: Could not reflect: requested table(s) not
        available in Engine(...): (routes)

    on every fresh deploy — including when the operator was running the
    very command meant to create that table. The fix makes ``_entry``
    return early when ``ctx.invoked_subcommand == "schema"``; this test
    pins that behavior with a counter, so a future refactor that
    accidentally drops the early-return regresses loudly.
    """
    call_count = {"build_services": 0, "load_settings": 0}

    def _spy_build(*_a: Any, **_kw: Any) -> MagicMock:
        call_count["build_services"] += 1
        return MagicMock(name="ServicesBundle")

    def _spy_load() -> object:
        call_count["load_settings"] += 1
        return object()

    monkeypatch.setattr(cli_module, "build_services", _spy_build)
    monkeypatch.setattr(cli_module, "_load_settings", _spy_load)

    # Use --help so we don't need to wire a real DB to exercise the
    # callback path; --help in the leaf still runs every parent callback.
    result = runner.invoke(app, ["schema", "migrate", "--help"])
    assert result.exit_code == 0, f"--help exit {result.exit_code}:\n{result.output}"
    assert call_count["build_services"] == 0, (
        "build_services was called for 'schema migrate' — chicken-and-egg "
        "regression. _entry must skip the bootstrap when ctx.invoked_subcommand "
        "== 'schema' so the bootstrap command can run on a fresh deploy."
    )
    assert call_count["load_settings"] == 0, (
        "_load_settings was called for 'schema migrate'. The schema subapp "
        "loads its own settings via _load_settings_for_migrate; the root "
        "callback should leave them alone."
    )


def test_entry_calls_build_services_for_non_schema_subcommand(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity baseline for the schema-skip regression above: non-schema
    subcommands MUST still go through the normal bootstrap. Without this
    counterpart, the regression test could pass trivially even if _entry
    were broken into never calling build_services for any command."""
    call_count = {"build_services": 0}

    def _spy_build(*_a: Any, **_kw: Any) -> MagicMock:
        call_count["build_services"] += 1
        return MagicMock(name="ServicesBundle")

    monkeypatch.setattr(cli_module, "build_services", _spy_build)
    monkeypatch.setattr(cli_module, "_load_settings", lambda: object())

    result = runner.invoke(app, ["check", "--help"])
    assert result.exit_code == 0, f"--help exit {result.exit_code}:\n{result.output}"
    assert call_count["build_services"] == 1, (
        f"build_services was called {call_count['build_services']} times for "
        "'check --help'; expected exactly 1. Non-schema commands must still "
        "go through the normal bootstrap."
    )
