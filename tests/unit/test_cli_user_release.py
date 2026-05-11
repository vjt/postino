"""postino user release CLI surface.

Unit-tests for `user release`: the inverse of `user passwd --claim`.
Drives every branch of the release-to-IdP decision through a mocked
ServicesBundle. No DB, no real identity provider — the CLI logic is
the only thing under test.

Mirrors the fixtures in ``test_cli_user_passwd_claim.py`` exactly so
both files keep evolving in lockstep.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from postino import cli as cli_module
from postino.cli import app
from postino_core.errors import NotFoundError


@pytest.fixture
def runner() -> CliRunner:
    # Click 8.2+ captures stderr separately on `result.stderr` by default;
    # no `mix_stderr=False` argument needed.
    return CliRunner()


@pytest.fixture
def mock_services(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace the CLI bootstrap with a MagicMock ServicesBundle.

    Default: ``supports_release_to_noauth`` is True (hybrid backend).
    Individual tests override to model LocalProvider rejection.
    """
    bundle = MagicMock(name="ServicesBundle")
    bundle.identity.supports_release_to_noauth.return_value = True

    def _fake_load_settings() -> object:
        return object()

    def _fake_build_services(*_a: Any, **_kw: Any) -> MagicMock:
        return bundle

    monkeypatch.setattr(cli_module, "_load_settings", _fake_load_settings)
    monkeypatch.setattr(cli_module, "build_services", _fake_build_services)
    return bundle


def test_release_on_hash_succeeds(runner: CliRunner, mock_services: MagicMock) -> None:
    """Row has a real password hash; release writes the sentinel + audit row."""
    mock_services.mailbox.is_idp_managed.return_value = False
    result = runner.invoke(app, ["user", "release", "u@x.io"])
    assert result.exit_code == 0, result.stderr
    assert "released to IdP" in result.stderr
    mock_services.mailbox.release_identity.assert_called_once_with("u@x.io")


def test_release_on_sentinel_is_idempotent(runner: CliRunner, mock_services: MagicMock) -> None:
    """Row already at {NOAUTH}: print info, exit 0, do not write audit."""
    mock_services.mailbox.is_idp_managed.return_value = True
    result = runner.invoke(app, ["user", "release", "u@x.io"])
    assert result.exit_code == 0, result.stderr
    assert "already IdP-managed" in result.stderr
    mock_services.mailbox.release_identity.assert_not_called()


def test_release_under_local_backend_refuses(runner: CliRunner, mock_services: MagicMock) -> None:
    """LocalProvider rejects release; CLI surfaces ConfigError as non-zero."""
    mock_services.identity.supports_release_to_noauth.return_value = False
    result = runner.invoke(app, ["user", "release", "u@x.io"])
    assert result.exit_code != 0
    assert "identity_backend=hybrid" in result.stderr
    mock_services.mailbox.release_identity.assert_not_called()


def test_release_missing_user_returns_error(runner: CliRunner, mock_services: MagicMock) -> None:
    """is_idp_managed raises NotFoundError; CLI surfaces non-zero exit."""
    mock_services.mailbox.is_idp_managed.side_effect = NotFoundError("missing@x.io")
    result = runner.invoke(app, ["user", "release", "missing@x.io"])
    assert result.exit_code != 0
    mock_services.mailbox.release_identity.assert_not_called()
