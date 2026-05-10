"""postino user … subcommands."""

from __future__ import annotations

from typing import Annotated, cast

import typer
from pydantic import BaseModel, SecretStr

from postino_core.enums import MailboxStatus, PasswordScheme
from postino_core.models import MailboxCreate
from postino_core.output import Renderer
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
    username: str,
    password: Annotated[str, typer.Option("--password", help="Password to set.")],
    name: Annotated[str, typer.Option("--name", help="Display name.")] = "",
    quota: Annotated[
        str,
        typer.Option("--quota", help="Quota size, e.g. 5G or 0 for unlimited."),
    ] = "0",
    scheme: Annotated[
        PasswordScheme,
        typer.Option("--scheme", help="Hash scheme."),
    ] = PasswordScheme.BCRYPT,
) -> None:
    """Create a mailbox."""
    from postino.cli import exit_with_error as _exit
    from postino_core.errors import MailctlError

    try:
        s = _services(ctx)
        m = s.mailbox.add(
            MailboxCreate(
                username=username,
                password=SecretStr(password),
                name=name,
                quota_bytes=parse_quota(quota),
                scheme=scheme,
            )
        )
        _renderer(ctx).render(m)
    except MailctlError as e:
        _exit(e)


@app.command("del")
def delete(
    ctx: typer.Context,
    username: str,
    keep_maildir: Annotated[bool, typer.Option("--keep-maildir/--remove-maildir")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Delete a mailbox."""
    from postino.cli import exit_with_error as _exit
    from postino_core.errors import MailctlError

    if not yes:
        typer.confirm(f"Delete mailbox {username}?", abort=True)
    try:
        _services(ctx).mailbox.delete(username, keep_maildir=keep_maildir)
    except MailctlError as e:
        _exit(e)


@app.command("list")
def list_(
    ctx: typer.Context,
    domain: Annotated[str, typer.Option("--domain")] = "",
    include_disabled: Annotated[bool, typer.Option("--all/--enabled-only")] = False,
) -> None:
    """List mailboxes."""
    s = _services(ctx)
    items = s.mailbox.list(
        domain=domain or None,
        include_disabled=include_disabled,
    )
    _renderer(ctx).render(cast(list[BaseModel], items))


@app.command("show")
def show(ctx: typer.Context, username: str) -> None:
    """Show one mailbox."""
    from postino.cli import exit_with_error as _exit
    from postino_core.errors import MailctlError, NotFoundError

    try:
        m = _services(ctx).mailbox.get(username)
        if m is None:
            raise NotFoundError(f"mailbox {username} does not exist")
        _renderer(ctx).render(m)
    except MailctlError as e:
        _exit(e)


@app.command("passwd")
def passwd(
    ctx: typer.Context,
    username: str,
    password: Annotated[str, typer.Option("--password")],
    scheme: Annotated[PasswordScheme, typer.Option("--scheme")] = PasswordScheme.BCRYPT,
) -> None:
    """Change password (local backend only; hidden in zitadel mode)."""
    from postino.cli import exit_with_error as _exit
    from postino_core.errors import ConfigError, MailctlError

    try:
        s = _services(ctx)
        if not s.identity.supports_password_change():
            raise ConfigError("password change not supported by current identity backend")
        s.mailbox.set_password(username, SecretStr(password), scheme)
    except MailctlError as e:
        _exit(e)


@app.command("enable")
def enable(ctx: typer.Context, username: str) -> None:
    """Set status=ACTIVE."""
    _services(ctx).mailbox.set_status(username, MailboxStatus.ACTIVE)


@app.command("disable")
def disable(ctx: typer.Context, username: str) -> None:
    """Set status=DISABLED."""
    _services(ctx).mailbox.set_status(username, MailboxStatus.DISABLED)


@app.command("quota")
def quota_cmd(
    ctx: typer.Context,
    username: str,
    set_value: Annotated[str, typer.Option("--set", help="New quota, e.g. 5G.")] = "",
) -> None:
    """Show or set quota cap."""
    s = _services(ctx)
    if set_value:
        s.mailbox.set_quota(username, parse_quota(set_value))
    m = s.mailbox.get(username)
    if m is None:
        from postino.cli import exit_with_error as _exit
        from postino_core.errors import NotFoundError

        _exit(NotFoundError(f"mailbox {username} does not exist"))
    _renderer(ctx).render(m)  # type: ignore[arg-type]
