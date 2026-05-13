"""In-process CLI tests for `postino list`. Service is mocked via
ctx.obj injection; subprocess paths are exercised in the e2e suite."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import typer
from typer.testing import CliRunner

from postino.commands import list as list_cmd
from postino_core.models import MailingList

runner = CliRunner()


def _ctx(mailing_list_svc: object) -> dict[str, object]:
    services = MagicMock()
    services.mailing_list = mailing_list_svc
    return {"services": services, "json": False, "quiet": False, "no_color": False}


def _make_app(svc: object) -> typer.Typer:
    app = typer.Typer()
    app.add_typer(list_cmd.app, name="list")

    @app.callback()
    def _cb(ctx: typer.Context) -> None:  # pyright: ignore[reportUnusedFunction]
        ctx.obj = _ctx(svc)

    return app


def test_list_add_calls_service_with_owners(tmp_path: Path) -> None:
    svc = MagicMock()
    svc.add.return_value = MailingList(
        address="team@lists.example.org",
        owners=["alice@example.org", "bob@example.org"],
        subscriber_count=0,
        spool_dir=tmp_path,
    )
    app = _make_app(svc)
    r = runner.invoke(
        app,
        [
            "list",
            "add",
            "team@lists.example.org",
            "--owner",
            "alice@example.org",
            "--owner",
            "bob@example.org",
        ],
    )
    assert r.exit_code == 0, r.output
    payload = svc.add.call_args[0][0]
    assert payload.address == "team@lists.example.org"
    assert payload.owners == ["alice@example.org", "bob@example.org"]


def test_list_add_requires_at_least_one_owner() -> None:
    svc = MagicMock()
    app = _make_app(svc)
    r = runner.invoke(app, ["list", "add", "team@lists.example.org"])
    assert r.exit_code != 0


def test_list_sub_calls_service() -> None:
    svc = MagicMock()
    app = _make_app(svc)
    r = runner.invoke(app, ["list", "sub", "team@lists.example.org", "bob@example.org"])
    assert r.exit_code == 0, r.output
    svc.subscribe.assert_called_once_with(address="team@lists.example.org", email="bob@example.org")


def test_list_unsub_calls_service() -> None:
    svc = MagicMock()
    app = _make_app(svc)
    r = runner.invoke(app, ["list", "unsub", "team@lists.example.org", "bob@example.org"])
    assert r.exit_code == 0, r.output
    svc.unsubscribe.assert_called_once_with(
        address="team@lists.example.org", email="bob@example.org"
    )


def test_list_show_renders_model(tmp_path: Path) -> None:
    svc = MagicMock()
    svc.get.return_value = MailingList(
        address="team@lists.example.org",
        owners=["alice@example.org"],
        subscriber_count=3,
        spool_dir=tmp_path,
    )
    app = _make_app(svc)
    # COLUMNS=200: Rich defaults to 80 cols in CI/pipe; address cell gets truncated otherwise.
    r = runner.invoke(app, ["list", "show", "team@lists.example.org"], env={"COLUMNS": "200"})
    assert r.exit_code == 0
    assert "team@lists.example.org" in r.output


def test_list_show_missing_exits_not_found() -> None:
    svc = MagicMock()
    svc.get.return_value = None
    app = _make_app(svc)
    r = runner.invoke(app, ["list", "show", "missing@lists.example.org"])
    assert r.exit_code == 1


def test_list_ls_renders_all(tmp_path: Path) -> None:
    svc = MagicMock()
    svc.list_all.return_value = [
        MailingList(
            address="team@lists.example.org",
            owners=["alice@example.org"],
            subscriber_count=2,
            spool_dir=tmp_path,
        ),
    ]
    app = _make_app(svc)
    # COLUMNS=200: prevent Rich table truncation in pipe/CI environments.
    r = runner.invoke(app, ["list", "ls"], env={"COLUMNS": "200"})
    assert r.exit_code == 0
    svc.list_all.assert_called_once_with(domain=None)


def test_list_ls_with_domain_filter(tmp_path: Path) -> None:
    svc = MagicMock()
    svc.list_all.return_value = []
    app = _make_app(svc)
    r = runner.invoke(app, ["list", "ls", "--domain", "lists.example.org"])
    assert r.exit_code == 0
    svc.list_all.assert_called_once_with(domain="lists.example.org")


def test_list_rm_calls_service_with_force() -> None:
    svc = MagicMock()
    app = _make_app(svc)
    r = runner.invoke(app, ["list", "rm", "team@lists.example.org", "--force", "--yes"])
    assert r.exit_code == 0, r.output
    svc.delete.assert_called_once_with("team@lists.example.org", force=True)


def test_list_rm_requires_yes_or_prompts() -> None:
    svc = MagicMock()
    app = _make_app(svc)
    # No --yes: typer.confirm aborts with 'n' input.
    r = runner.invoke(app, ["list", "rm", "team@lists.example.org"], input="n\n")
    assert r.exit_code != 0
    svc.delete.assert_not_called()


def test_list_when_service_unset_exits_config_error() -> None:
    """When mailing_list is None (mlmmj_spool_dir not configured) every command
    must surface ConfigError → exit code 4."""
    app = _make_app(None)
    r = runner.invoke(
        app,
        ["list", "add", "team@lists.example.org", "--owner", "alice@example.org"],
    )
    assert r.exit_code == 4
    assert "POSTINO_MLMMJ_SPOOL_DIR" in r.output
