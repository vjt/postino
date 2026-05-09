"""postino alias … subcommands."""
from __future__ import annotations

from typing import Annotated, cast

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


@app.command("add")
def add(ctx: typer.Context, address: str, goto: str) -> None:
    from postino.cli import exit_with_error as _exit
    try:
        a = _services(ctx).alias.add(address=address, goto=goto)
        _renderer(ctx).render(a)
    except MailctlError as e:
        _exit(e)


@app.command("del")
def delete(
    ctx: typer.Context,
    address: str,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    from postino.cli import exit_with_error as _exit
    if not yes:
        typer.confirm(f"Delete alias {address}?", abort=True)
    try:
        _services(ctx).alias.delete(address)
    except MailctlError as e:
        _exit(e)


@app.command("list")
def list_(
    ctx: typer.Context,
    domain: Annotated[str, typer.Option("--domain")] = "",
) -> None:
    items = _services(ctx).alias.list(domain=domain or None)
    _renderer(ctx).render(cast(list[BaseModel], items))
