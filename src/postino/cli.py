"""postino — Typer CLI entrypoint.

Top-level catches MailctlError → prints + exits with the documented
code. Anything else propagates to Rich's traceback handler and exits 99."""

from __future__ import annotations

import getpass
import os
from datetime import UTC, datetime

import typer
from pydantic import ValidationError
from rich.traceback import install as install_traceback

from postino.commands import alias as alias_cmd
from postino.commands import check as check_cmd
from postino.commands import domain as domain_cmd
from postino.commands import list as list_cmd
from postino.commands import quota as quota_cmd
from postino.commands import status as status_cmd
from postino.commands import user as user_cmd
from postino.exit import CliState, exit_with_error
from postino_core.config import PostinoSettings
from postino_core.errors import ConfigError, MailctlError
from postino_core.services.bundle import build_services

app = typer.Typer(
    no_args_is_help=True,
    add_completion=True,
    rich_markup_mode="rich",
    help="postino — administer a Postfix + Dovecot mail server (PostfixAdmin schema).",
)

app.add_typer(user_cmd.app, name="user", help="Mailbox CRUD.")
app.add_typer(alias_cmd.app, name="alias", help="Alias CRUD.")
app.add_typer(domain_cmd.app, name="domain", help="Domain CRUD.")
app.add_typer(list_cmd.app, name="list", help="Mailing-list (mlmmj) CRUD.")
app.add_typer(quota_cmd.app, name="quota", help="Quota inspection.")
app.command("check", help="Validate consistency between postino and the mail stack.")(check_cmd.run)
app.command("status", help="Snapshot of mail stack health.")(status_cmd.run)


def _load_settings() -> PostinoSettings:
    """Build PostinoSettings from TOML + env, with friendly error mapping.

    Translates pydantic-settings ValidationError into a human-readable
    ConfigError so the CLI can exit with code 4 and a useful message.
    """
    try:
        return PostinoSettings()  # type: ignore[call-arg]  # WHY: pydantic-settings raises ValidationError for missing fields; pyright thinks PostinoSettings() is missing args. Captured in PR-A6 cleanup.
    except ValidationError as e:
        from postino_core.config import config_toml_paths
        from postino_core.config_errors import (
            format_validation_error,
            load_toml_with_origin,
        )

        # Missing-required with no TOML at all: point operators at the
        # simplest "make it work" path (one env var) rather than dumping
        # raw pydantic errors. Once any TOML exists, fall through to
        # format_validation_error so the message names file:line:key.
        missing = [err for err in e.errors() if err["type"] == "missing"]
        if missing and not any(p.is_file() for p in config_toml_paths()):
            raise ConfigError(
                "config not found: set POSTINO_IDENTITY_BACKEND=local "
                "(or another POSTINO_* env var)\n"
                "  or write /usr/local/etc/postino/postino.toml or "
                "~/.config/postino/postino.toml.\n"
                f"  missing fields: {', '.join(str(err['loc'][0]) for err in missing)}"
            ) from e

        sources = load_toml_with_origin(list(config_toml_paths()))
        raise ConfigError(format_validation_error(e, sources)) from e


def _cli_actor() -> str:
    """OS user running the CLI; recorded in PA's `log.username` column.

    Falls back to ``"postino"`` if the env strip is so aggressive that
    getpass cannot resolve the calling user (rare; daemonised invocations
    with no controlling tty)."""
    try:
        return getpass.getuser()
    except OSError:
        return "postino"


def _version_callback(value: bool) -> None:
    if value:
        from importlib.metadata import version

        typer.echo(f"il-postino {version('il-postino')}")
        raise typer.Exit()


@app.callback()
def _entry(  # pyright: ignore[reportUnusedFunction]
    ctx: typer.Context,
    json: bool = typer.Option(False, "--json", help="Output JSON."),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress banners; data still printed."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colors."),
    _version: bool = typer.Option(
        False,
        "--version",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    install_traceback(show_locals=False)
    # NO_COLOR is the de-facto standard (no-color.org); CI=true is a
    # widely-adopted CI-runner convention. Either one disables color even
    # without the explicit flag.
    no_color_effective = (
        no_color or bool(os.environ.get("NO_COLOR")) or os.environ.get("CI", "").lower() == "true"
    )
    try:
        settings = _load_settings()
        services = build_services(
            settings,
            clock=lambda: datetime.now(UTC),
            echo=False,
            actor=_cli_actor,
        )
        state: CliState = {
            "services": services,
            "json": json,
            "quiet": quiet,
            "no_color": no_color_effective,
        }
        ctx.obj = state
    except MailctlError as e:
        exit_with_error(e)
