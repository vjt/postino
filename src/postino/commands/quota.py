"""postino quota … subcommands (read-only view of quota2 usage)."""

from __future__ import annotations

import typer

from postino.exit import exit_with_error, get_services
from postino.output import Renderer
from postino_core.errors import NotFoundError

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    epilog="Run `postino --help` for global options (--json, --quiet, --no-color).",
)


@app.command("show")
def show(ctx: typer.Context, username: str = "") -> None:
    """Show quota usage for one user, or all users if no argument."""
    if username:
        u = get_services(ctx).quota.show(username)
        if u is None:
            exit_with_error(NotFoundError(f"no quota row for {username}"))
        Renderer.from_ctx(ctx).render(u)
    else:
        items = get_services(ctx).quota.list()
        Renderer.from_ctx(ctx).render(items)
