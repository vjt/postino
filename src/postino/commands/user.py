"""postino user … subcommands."""

from __future__ import annotations

from typing import Annotated

import typer
from pydantic import SecretStr

from postino.exit import exit_with_error, get_services, is_json
from postino.output import Renderer
from postino_core.enums import MailboxStatus, PasswordScheme
from postino_core.errors import ConfigError, MailctlError, NotFoundError
from postino_core.models import MailboxCreate
from postino_core.quota import parse_quota


def _prompt_new_password(label: str = "Password") -> SecretStr:
    """Prompt for a password without echoing or persisting in argv.

    Click's ``hide_input`` wraps ``getpass`` so the password never lands
    in shell history, the kernel argv table, or ``ps`` output.
    Confirmation prompt avoids typo lock-outs."""
    pw = typer.prompt(label, hide_input=True, confirmation_prompt=True)
    if not pw:
        raise ConfigError(f"{label.lower()} cannot be empty")
    return SecretStr(pw)


app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command("add")
def add(
    ctx: typer.Context,
    username: str,
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
    """Create a mailbox.

    Prompts for the password twice. The password is never accepted on
    the command line: it would appear in shell history, the process
    tree, and CI logs.

    Refuses to run under ``identity_backend = "noauth"``: in that mode
    the external IdP owns provisioning (postinod / IdP admin UI), and
    the mailbox row is later reconciled — letting `user add` create one
    here would silently bypass the IdP.
    """
    try:
        s = get_services(ctx)
        if not s.identity.supports_local_provisioning():
            raise ConfigError(
                "identity_backend=noauth: provision mailboxes via the external IdP, "
                "not `postino user add`"
            )
        password = _prompt_new_password()
        m = s.mailbox.add(
            MailboxCreate(
                username=username,
                password=password,
                name=name,
                quota_bytes=parse_quota(quota),
                scheme=scheme,
            )
        )
        Renderer(json=is_json(ctx)).render(m)
    except MailctlError as e:
        exit_with_error(e)


@app.command("del")
def delete(
    ctx: typer.Context,
    username: str,
    keep_maildir: Annotated[bool, typer.Option("--keep-maildir/--remove-maildir")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Delete a mailbox."""
    if not yes:
        typer.confirm(f"Delete mailbox {username}?", abort=True)
    try:
        get_services(ctx).mailbox.delete(username, keep_maildir=keep_maildir)
    except MailctlError as e:
        exit_with_error(e)


@app.command("list")
def list_(
    ctx: typer.Context,
    domain: Annotated[str, typer.Option("--domain")] = "",
    include_disabled: Annotated[bool, typer.Option("--all/--enabled-only")] = False,
) -> None:
    """List mailboxes."""
    s = get_services(ctx)
    items = s.mailbox.list(
        domain=domain or None,
        include_disabled=include_disabled,
    )
    Renderer(json=is_json(ctx)).render(items)


@app.command("show")
def show(ctx: typer.Context, username: str) -> None:
    """Show one mailbox."""
    try:
        m = get_services(ctx).mailbox.get(username)
        if m is None:
            raise NotFoundError(f"mailbox {username} does not exist")
        Renderer(json=is_json(ctx)).render(m)
    except MailctlError as e:
        exit_with_error(e)


@app.command("passwd")
def passwd(
    ctx: typer.Context,
    username: str,
    scheme: Annotated[PasswordScheme, typer.Option("--scheme")] = PasswordScheme.BCRYPT,
    claim: Annotated[
        bool,
        typer.Option(
            "--claim",
            help=(
                "Claim an IdP-managed mailbox into SQL auth (required when "
                "the current password is {NOAUTH})."
            ),
        ),
    ] = False,
) -> None:
    """Change password.

    Prompts for the new password twice. As with `user add`, the password
    is never accepted on the command line. Under identity_backend=hybrid,
    rotating the password on a mailbox currently holding {NOAUTH}
    requires --claim (the row transitions from IdP-auth to SQL-auth).
    """
    try:
        s = get_services(ctx)
        if not s.identity.supports_password_change():
            raise ConfigError("password change not supported by current identity backend")
        current = s.mailbox.get(username)
        if current is None:
            raise NotFoundError(f"mailbox {username} does not exist")
        is_sentinel = s.mailbox.is_idp_managed(username)
        if is_sentinel and not claim:
            typer.echo(
                f"{username} is currently IdP-managed ({{NOAUTH}}); pass --claim to "
                "transition it into SQL auth.",
                err=True,
            )
            raise typer.Exit(code=2)
        password = _prompt_new_password("New password")
        s.mailbox.set_password(username, password, scheme)
        if is_sentinel:
            typer.echo(f"{username} claimed into SQL auth.", err=True)
    except MailctlError as e:
        exit_with_error(e)


@app.command("enable")
def enable(ctx: typer.Context, username: str) -> None:
    """Set status=ACTIVE."""
    try:
        get_services(ctx).mailbox.set_status(username, MailboxStatus.ACTIVE)
    except MailctlError as e:
        exit_with_error(e)


@app.command("disable")
def disable(ctx: typer.Context, username: str) -> None:
    """Set status=DISABLED."""
    try:
        get_services(ctx).mailbox.set_status(username, MailboxStatus.DISABLED)
    except MailctlError as e:
        exit_with_error(e)


@app.command("quota")
def quota_cmd(
    ctx: typer.Context,
    username: str,
    set_value: Annotated[str, typer.Option("--set", help="New quota, e.g. 5G.")] = "",
) -> None:
    """Show or set quota cap."""
    try:
        s = get_services(ctx)
        if set_value:
            s.mailbox.set_quota(username, parse_quota(set_value))
        m = s.mailbox.get(username)
        if m is None:
            raise NotFoundError(f"mailbox {username} does not exist")
        Renderer(json=is_json(ctx)).render(m)
    except MailctlError as e:
        exit_with_error(e)
