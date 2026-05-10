"""postino domain … subcommands."""

from __future__ import annotations

from typing import Annotated, cast

import typer
from pydantic import BaseModel

from postino.output import Renderer
from postino_core.enums import DomainTransport
from postino_core.errors import MailctlError
from postino_core.quota import parse_quota
from postino_core.services.bundle import ServicesBundle

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _services(ctx: typer.Context) -> ServicesBundle:
    return ctx.obj["services"]  # type: ignore[no-any-return]


def _renderer(ctx: typer.Context) -> Renderer:
    return Renderer(json=bool(ctx.obj["json"]))


@app.command("add")
def add(
    ctx: typer.Context,
    domain: str,
    description: Annotated[str, typer.Option("--description")] = "",
    max_aliases: Annotated[int, typer.Option("--max-aliases")] = 0,
    max_mailboxes: Annotated[int, typer.Option("--max-mailboxes")] = 0,
    max_quota: Annotated[str, typer.Option("--max-quota")] = "0",
    default_quota: Annotated[str, typer.Option("--default-quota")] = "1G",
    transport: Annotated[DomainTransport, typer.Option("--transport")] = DomainTransport.VIRTUAL,
    backupmx: Annotated[bool, typer.Option("--backupmx/--no-backupmx")] = False,
) -> None:
    from postino.cli import exit_with_error as _exit

    try:
        d = _services(ctx).domain.add(
            domain=domain,
            description=description,
            max_aliases=max_aliases,
            max_mailboxes=max_mailboxes,
            max_quota_bytes=parse_quota(max_quota),
            default_quota_bytes=parse_quota(default_quota),
            transport=transport,
            backupmx=backupmx,
        )
        _renderer(ctx).render(d)
    except MailctlError as e:
        _exit(e)


@app.command("del")
def delete(
    ctx: typer.Context,
    domain: str,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Cascade-delete mailboxes, aliases, and admins owned by this domain.",
        ),
    ] = False,
) -> None:
    from postino.cli import exit_with_error as _exit

    if not yes:
        typer.confirm(f"Delete domain {domain}?", abort=True)
    try:
        _services(ctx).domain.delete(domain, force=force)
    except MailctlError as e:
        _exit(e)


@app.command("list")
def list_(ctx: typer.Context) -> None:
    items = _services(ctx).domain.list()
    _renderer(ctx).render(cast(list[BaseModel], items))
