"""postino — Typer CLI entrypoint.

Top-level catches MailctlError → prints + exits with the documented
code. Anything else propagates to Rich's traceback handler and exits 99."""

from __future__ import annotations

import getpass
from datetime import UTC, datetime

import typer
from pydantic import ValidationError
from rich.traceback import install as install_traceback

from postino.commands import alias as alias_cmd
from postino.commands import check as check_cmd
from postino.commands import domain as domain_cmd
from postino.commands import list as list_cmd
from postino.commands import quota as quota_cmd
from postino.commands import reconcile as reconcile_cmd
from postino.commands import status as status_cmd
from postino.commands import user as user_cmd
from postino.exit import CliState, exit_with_error
from postino_core.config import PostinoSettings
from postino_core.errors import ConfigError, MailctlError
from postino_core.services.bundle import build_services

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
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
app.command("reconcile", help="(V2) drift detection vs identity source.")(reconcile_cmd.run)


def _load_settings() -> PostinoSettings:
    """Build PostinoSettings from TOML + env, with friendly error mapping.

    Translates pydantic-settings ValidationError into a human-readable
    ConfigError so the CLI can exit with code 4 and a useful message.
    """
    try:
        return PostinoSettings()  # type: ignore[call-arg]  # WHY: pydantic-settings raises ValidationError for missing fields; pyright thinks PostinoSettings() is missing args. Captured in PR-A6 cleanup.
    except ValidationError as e:
        missing = [err["loc"][0] for err in e.errors() if err["type"] == "missing"]
        if missing:
            fields = ", ".join(str(m) for m in missing)
            raise ConfigError(
                f"missing required config: {fields}.\n"
                "  Set POSTINO_* env vars (e.g. POSTINO_IDENTITY_BACKEND=local)\n"
                "  or write /usr/local/etc/postino/postino.toml or "
                "~/.config/postino/postino.toml.\n"
                "  See `postino --help` and the README for the full schema."
            ) from e
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        raise ConfigError(f"invalid config: {details}") from e


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
    _version: bool = typer.Option(
        False,
        "--version",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    install_traceback(show_locals=False)
    try:
        settings = _load_settings()
        services = build_services(
            settings,
            clock=lambda: datetime.now(UTC),
            echo=False,
            actor=_cli_actor,
        )
        state: CliState = {"services": services, "json": json}
        ctx.obj = state
    except MailctlError as e:
        exit_with_error(e)
