"""postino alias … subcommands."""

from __future__ import annotations

from typing import Annotated

import typer

from postino.exit import exit_with_error, get_services, is_json
from postino.output import Renderer
from postino_core.errors import MailctlError

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command("add")
def add(ctx: typer.Context, address: str, goto: str) -> None:
    try:
        a = get_services(ctx).alias.add(address=address, goto=goto)
        Renderer(json=is_json(ctx)).render(a)
    except MailctlError as e:
        exit_with_error(e)


@app.command("del")
def delete(
    ctx: typer.Context,
    address: str,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    if not yes:
        typer.confirm(f"Delete alias {address}?", abort=True)
    try:
        get_services(ctx).alias.delete(address)
    except MailctlError as e:
        exit_with_error(e)


@app.command("list")
def list_(
    ctx: typer.Context,
    domain: Annotated[str, typer.Option("--domain")] = "",
) -> None:
    items = get_services(ctx).alias.list(domain=domain or None)
    Renderer(json=is_json(ctx)).render(items)
