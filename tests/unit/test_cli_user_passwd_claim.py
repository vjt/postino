"""postino user passwd --claim guard.

Unit-tests the CLI surface: every branch of the `--claim` decision
is driven through a mocked ServicesBundle. No real DB, no real
identity provider — the CLI logic is the only thing under test.

Note: SENTINEL_NOAUTH no longer needed at the CLI test layer because
the CLI consults `s.mailbox.is_idp_managed(...)` (a bool) rather than
inspecting raw passwords.
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
    # Click 8.2+ removed `mix_stderr`: stderr is captured separately by default
    # and surfaced on `result.stderr`. Older click bundled with typer<0.13
    # required `mix_stderr=False` for the same behaviour.
    return CliRunner()


@pytest.fixture
def mock_services(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace the CLI bootstrap with a MagicMock ServicesBundle.

    The real `_entry` callback would `_load_settings()` + `build_services()`
    — both of which require a working DB and config. We short-circuit
    both so each test injects exactly the behaviour it cares about on
    the returned mock.
    """
    bundle = MagicMock(name="ServicesBundle")
    # The CLI callback reads `s.identity.supports_password_change()`; the
    # default MagicMock returns truthy, but be explicit so a future change
    # of the predicate's call shape surfaces here.
    bundle.identity.supports_password_change.return_value = True

    def _fake_load_settings() -> object:
        return object()

    def _fake_build_services(*_a: Any, **_kw: Any) -> MagicMock:
        return bundle

    monkeypatch.setattr(cli_module, "_load_settings", _fake_load_settings)
    monkeypatch.setattr(cli_module, "build_services", _fake_build_services)
    return bundle


def test_passwd_on_sentinel_without_claim_refuses(
    runner: CliRunner, mock_services: MagicMock
) -> None:
    mock_services.mailbox.is_idp_managed.return_value = True
    mock_services.mailbox.get.return_value = MagicMock()  # exists
    result = runner.invoke(app, ["user", "passwd", "u@x.io"], input="hunter2\nhunter2\n")
    assert result.exit_code != 0
    assert "currently IdP-managed" in result.stderr
    assert "--claim" in result.stderr
    mock_services.mailbox.set_password.assert_not_called()


def test_passwd_on_sentinel_with_claim_succeeds(
    runner: CliRunner, mock_services: MagicMock
) -> None:
    mock_services.mailbox.is_idp_managed.return_value = True
    mock_services.mailbox.get.return_value = MagicMock()
    result = runner.invoke(app, ["user", "passwd", "u@x.io", "--claim"], input="hunter2\nhunter2\n")
    assert result.exit_code == 0
    assert "claimed into SQL auth" in result.stderr
    mock_services.mailbox.set_password.assert_called_once()


def test_passwd_on_hash_no_warning(runner: CliRunner, mock_services: MagicMock) -> None:
    mock_services.mailbox.is_idp_managed.return_value = False
    mock_services.mailbox.get.return_value = MagicMock()
    result = runner.invoke(app, ["user", "passwd", "u@x.io"], input="hunter2\nhunter2\n")
    assert result.exit_code == 0
    assert "claim" not in result.stderr
