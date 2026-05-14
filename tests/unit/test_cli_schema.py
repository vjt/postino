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
