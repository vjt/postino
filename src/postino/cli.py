"""postino — Typer CLI entrypoint.

Top-level catches MailctlError → prints + exits with the documented
code. Anything else propagates to Rich's traceback handler and exits 99."""
from __future__ import annotations

import os
import sys
from datetime import datetime

import typer
from rich.console import Console
from rich.traceback import install as install_traceback

from postino.commands import alias as alias_cmd
from postino.commands import check as check_cmd
from postino.commands import domain as domain_cmd
from postino.commands import quota as quota_cmd
from postino.commands import reconcile as reconcile_cmd
from postino.commands import status as status_cmd
from postino.commands import user as user_cmd
from postino_core.config import PostinoSettings
from postino_core.errors import (
    AlreadyExistsError,
    CapacityError,
    ConfigError,
    DBError,
    FilesystemError,
    HookError,
    MailctlError,
    NotFoundError,
)
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
app.add_typer(quota_cmd.app, name="quota", help="Quota inspection.")
app.command("check", help="Validate consistency between postino and the mail stack.")(
    check_cmd.run
)
app.command("status", help="Snapshot of mail stack health.")(status_cmd.run)
app.command("reconcile", help="(V2) drift detection vs identity source.")(
    reconcile_cmd.run
)


_EXIT_CODES: dict[type[MailctlError], int] = {
    NotFoundError: 1,
    AlreadyExistsError: 2,
    CapacityError: 3,
    ConfigError: 4,
    DBError: 5,
    FilesystemError: 6,
    HookError: 7,
}


def _settings_with_db_override() -> PostinoSettings:
    """Test-only escape hatch: POSTINO_DB_URL_OVERRIDE forces the engine URL.

    In production this env var is never set; settings load from TOML/env."""
    s = PostinoSettings()  # type: ignore[call-arg]
    override = os.environ.get("POSTINO_DB_URL_OVERRIDE")
    if override:
        sql_cf = s.postfix_sql_dir / "sql-virtual_mailbox_maps.cf"
        body = override.replace("mysql+pymysql://", "")
        auth, _, hostdb = body.partition("@")
        user, _, pwd = auth.partition(":")
        host, _, dbname = hostdb.partition("/")
        sql_cf.write_text(
            f"hosts = {host}\nuser = {user}\npassword = {pwd}\ndbname = {dbname}\n"
        )
    return s


@app.callback()
def _entry(  # pyright: ignore[reportUnusedFunction]
    ctx: typer.Context,
    json: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    install_traceback(show_locals=False)
    settings = _settings_with_db_override()
    services = build_services(
        settings, clock=lambda: datetime.now(), echo=False
    )
    ctx.obj = {"services": services, "json": json}


def exit_with_error(err: MailctlError) -> None:
    """Print err to stderr and sys.exit with the documented exit code."""
    console = Console(stderr=True)
    console.print(f"[red]error:[/red] {err}")
    code = next(
        (c for cls, c in _EXIT_CODES.items() if isinstance(err, cls)),
        99,
    )
    sys.exit(code)


# Legacy alias kept for internal callers.
_exit = exit_with_error
