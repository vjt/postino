"""postino list … subcommands.

Module name shadows the ``list`` builtin; importers should use
``from postino.commands import list as list_cmd``.
"""

from __future__ import annotations

from typing import Annotated

import typer

from postino.exit import exit_with_error, get_services
from postino.output import Renderer
from postino_core.errors import ConfigError, MailctlError, NotFoundError
from postino_core.models import MailingListCreate
from postino_core.services.mailing_list import MailingListService

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    epilog="Run `postino --help` for global options (--json, --quiet, --no-color).",
)


def _ml_service(ctx: typer.Context) -> MailingListService:
    svc = get_services(ctx).mailing_list
    if svc is None:
        raise ConfigError(
            "mlmmj not configured: set POSTINO_MLMMJ_SPOOL_DIR (see docs/postino-mlmmj.md)"
        )
    return svc


@app.command("add", help="Create a new mlmmj mailing list. Domain must have transport=mlmmj.")
def add(
    ctx: typer.Context,
    address: str,
    owner: Annotated[
        list[str] | None, typer.Option("--owner", help="Owner email; pass once per owner.")
    ] = None,
) -> None:
    owners: list[str] = owner or []
    if not owners:
        # Mirror Pydantic's validator at the CLI boundary so the error message
        # is friendly instead of a pydantic ValidationError dump.
        raise typer.BadParameter("at least one --owner is required")
    try:
        ml = _ml_service(ctx).add(MailingListCreate(address=address, owners=owners))
        Renderer.from_ctx(ctx).render(ml)
    except MailctlError as e:
        exit_with_error(e)


@app.command("sub", help="Subscribe an email to a list. Idempotent.")
def sub(
    ctx: typer.Context,
    address: str,
    email: str,
) -> None:
    try:
        _ml_service(ctx).subscribe(address=address, email=email)  # type: ignore[arg-type]  # WHY: EmailStr accepts str at the boundary
    except MailctlError as e:
        exit_with_error(e)


@app.command("unsub", help="Unsubscribe an email from a list. Idempotent.")
def unsub(
    ctx: typer.Context,
    address: str,
    email: str,
) -> None:
    try:
        _ml_service(ctx).unsubscribe(address=address, email=email)  # type: ignore[arg-type]  # WHY: EmailStr accepts str at the boundary
    except MailctlError as e:
        exit_with_error(e)


@app.command("show", help="Owners + subscriber count + spool path for one list.")
def show(ctx: typer.Context, address: str) -> None:
    try:
        ml = _ml_service(ctx).get(address)  # type: ignore[arg-type]  # WHY: EmailStr at boundary
        if ml is None:
            raise NotFoundError(f"mailing list {address!r} does not exist")
        Renderer.from_ctx(ctx).render(ml)
    except MailctlError as e:
        exit_with_error(e)


@app.command("ls", help="List all mlmmj lists; --domain filters by FQDN.")
def ls(
    ctx: typer.Context,
    domain: Annotated[str | None, typer.Option("--domain")] = None,
) -> None:
    try:
        items = _ml_service(ctx).list_all(domain=domain)
        Renderer.from_ctx(ctx).render(items)
    except MailctlError as e:
        exit_with_error(e)


@app.command("rm", help="Delete a mailing list. Refuses non-empty lists unless --force.")
def rm(
    ctx: typer.Context,
    address: str,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Cascade-delete even when subscribers > 0.",
        ),
    ] = False,
) -> None:
    if not yes:
        typer.confirm(f"Delete mailing list {address}?", abort=True)
    try:
        _ml_service(ctx).delete(address, force=force)  # type: ignore[arg-type]  # WHY: EmailStr at boundary
    except MailctlError as e:
        exit_with_error(e)
