"""postino domain … subcommands."""

from __future__ import annotations

from typing import Annotated

import typer

from postino.exit import exit_with_error, get_services
from postino.output import Renderer
from postino_core.enums import DomainTransport, MailboxStatus
from postino_core.errors import MailctlError
from postino_core.quota import parse_quota

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    epilog="Run `postino --help` for global options (--json, --quiet, --no-color).",
)


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
    try:
        services = get_services(ctx)
        d = services.domain.add(
            domain=domain,
            description=description,
            max_aliases=max_aliases,
            max_mailboxes=max_mailboxes,
            max_quota_bytes=parse_quota(max_quota),
            default_quota_bytes=parse_quota(default_quota),
            transport=transport,
            backupmx=backupmx,
        )
        Renderer.from_ctx(ctx).render(d)
    except MailctlError as e:
        exit_with_error(e)


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
    keep_maildir: Annotated[
        bool,
        typer.Option(
            "--keep-maildir",
            help=(
                "Skip removing the per-domain maildir tree on disk. "
                "Useful when archiving the tree before final disposal."
            ),
        ),
    ] = False,
) -> None:
    if not yes:
        typer.confirm(f"Delete domain {domain}?", abort=True)
    try:
        get_services(ctx).domain.delete(domain, force=force, keep_maildir=keep_maildir)
    except MailctlError as e:
        exit_with_error(e)


@app.command("list")
def list_(ctx: typer.Context) -> None:
    items = get_services(ctx).domain.list()
    Renderer.from_ctx(ctx).render(items)


@app.command("enable")
def enable(ctx: typer.Context, name: str) -> None:
    """Set domain.active = 1."""
    try:
        get_services(ctx).domain.set_status(name, MailboxStatus.ACTIVE)
    except MailctlError as e:
        exit_with_error(e)


@app.command("disable")
def disable(ctx: typer.Context, name: str) -> None:
    """Set domain.active = 0."""
    try:
        get_services(ctx).domain.set_status(name, MailboxStatus.DISABLED)
    except MailctlError as e:
        exit_with_error(e)


alias_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Manage alias_domain rows",
    epilog="Run `postino --help` for global options (--json, --quiet, --no-color).",
)
app.add_typer(alias_app, name="alias")


@alias_app.command("add")
def alias_add(
    ctx: typer.Context,
    alias_domain: str,
    target: Annotated[str, typer.Option("--target", help="Target domain")],
) -> None:
    """Map alias_domain -> target."""
    try:
        row = get_services(ctx).alias_domain.add(alias_domain, target=target)
        Renderer.from_ctx(ctx).render(row)
    except MailctlError as e:
        exit_with_error(e)


@alias_app.command("list")
def alias_list(
    ctx: typer.Context,
    target: Annotated[str, typer.Option("--target", help="Filter by target_domain")] = "",
    include_disabled: Annotated[bool, typer.Option("--all/--enabled-only")] = False,
) -> None:
    rows = get_services(ctx).alias_domain.list(
        target=target or None,
        include_disabled=include_disabled,
    )
    Renderer.from_ctx(ctx).render(rows)


@alias_app.command("show")
def alias_show(ctx: typer.Context, alias_domain: str) -> None:
    try:
        row = get_services(ctx).alias_domain.get(alias_domain)
        Renderer.from_ctx(ctx).render(row)
    except MailctlError as e:
        exit_with_error(e)


@alias_app.command("del")
def alias_del(
    ctx: typer.Context,
    alias_domain: str,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    if not yes:
        typer.confirm(f"Delete alias_domain {alias_domain}?", abort=True)
    try:
        get_services(ctx).alias_domain.delete(alias_domain)
    except MailctlError as e:
        exit_with_error(e)


@alias_app.command("enable")
def alias_enable(ctx: typer.Context, alias_domain: str) -> None:
    """Set alias_domain.active = 1."""
    try:
        get_services(ctx).alias_domain.set_status(alias_domain, MailboxStatus.ACTIVE)
    except MailctlError as e:
        exit_with_error(e)


@alias_app.command("disable")
def alias_disable(ctx: typer.Context, alias_domain: str) -> None:
    """Set alias_domain.active = 0."""
    try:
        get_services(ctx).alias_domain.set_status(alias_domain, MailboxStatus.DISABLED)
    except MailctlError as e:
        exit_with_error(e)


@alias_app.command("retarget")
def alias_retarget(
    ctx: typer.Context,
    alias_domain: str,
    target: Annotated[str, typer.Option("--target", help="New target domain")],
) -> None:
    """Repoint alias_domain to a new target."""
    try:
        row = get_services(ctx).alias_domain.retarget(alias_domain, target=target)
        Renderer.from_ctx(ctx).render(row)
    except MailctlError as e:
        exit_with_error(e)
