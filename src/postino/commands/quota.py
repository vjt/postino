"""postino quota … subcommands (read-only view of quota2 usage)."""
from __future__ import annotations

from typing import cast

import typer
from pydantic import BaseModel

from postino_core.errors import MailctlError
from postino_core.output import Renderer
from postino_core.services.bundle import ServicesBundle

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _services(ctx: typer.Context) -> ServicesBundle:
    return ctx.obj["services"]  # type: ignore[no-any-return]


def _renderer(ctx: typer.Context) -> Renderer:
    return Renderer(json=bool(ctx.obj["json"]))


@app.command("show")
def show(ctx: typer.Context, username: str = "") -> None:
    """Show quota usage for one user, or all users if no argument."""
    if username:
        u = _services(ctx).quota.show(username)
        if u is None:
            from postino.cli import exit_with_error as _exit
            _exit(MailctlError(f"no quota row for {username}"))
        _renderer(ctx).render(cast(BaseModel, u))
    else:
        items = _services(ctx).quota.list()
        _renderer(ctx).render(cast(list[BaseModel], items))
