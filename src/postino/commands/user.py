"""postino user … subcommands."""

from __future__ import annotations

import sys
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


def _read_password_from_stdin() -> SecretStr:
    """Read one line from stdin; refuse if stdin is a TTY or empty.

    Used by ``--password-stdin``: enables scripted provisioning and
    rotation. Refuses interactive stdin because the keystrokes would
    echo to the terminal (no ``hide_input`` wrapper), and an empty
    line is treated as user error rather than silently provisioning a
    blank password.
    """
    if sys.stdin.isatty():
        raise ConfigError(
            "refusing to read password from interactive stdin; "
            "drop --password-stdin or pipe the password in"
        )
    line = sys.stdin.readline()
    pw = line.rstrip("\n")
    if not pw:
        raise ConfigError("empty password on stdin")
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
    password_stdin: Annotated[
        bool,
        typer.Option(
            "--password-stdin",
            help="Read password from stdin (one line). Refuses TTY input.",
        ),
    ] = False,
) -> None:
    """Create a mailbox.

    By default prompts for the password twice. Pass ``--password-stdin``
    to read one line from stdin (for scripting / config-management
    drivers); the flag refuses an interactive TTY so a typo cannot end
    up echoed to the terminal. The password is never accepted on the
    command line: it would appear in shell history, the process tree,
    and CI logs.

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
        password = _read_password_from_stdin() if password_stdin else _prompt_new_password()
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
        # WHY: is_idp_managed raises NotFoundError itself when the row is
        # absent (see postino_core/services/mailbox.py); a separate
        # mailbox.get() pre-check would be a redundant SQL roundtrip.
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


@app.command("release")
def release(ctx: typer.Context, username: str) -> None:
    """Release a mailbox's credential to the IdP ({NOAUTH} sentinel).

    Inverse of `user passwd --claim`. Idempotent: a row already on the
    sentinel returns success with an informational message and no audit
    row. Only meaningful under identity_backend=hybrid; LocalProvider
    raises ConfigError.
    """
    try:
        s = get_services(ctx)
        if not s.identity.supports_release_to_noauth():
            raise ConfigError(
                "release to IdP not supported under current identity backend; "
                "set identity_backend=hybrid in postino.toml"
            )
        # WHY: is_idp_managed raises NotFoundError itself when the row is
        # absent (see postino_core/services/mailbox.py); a separate
        # mailbox.get() pre-check would be a redundant SQL roundtrip
        # (same lesson as the passwd-claim refactor in commit cbcc4e2).
        if s.mailbox.is_idp_managed(username):
            typer.echo(f"{username} already IdP-managed; no change.", err=True)
            return
        s.mailbox.release_identity(username)
        typer.echo(f"{username} released to IdP-managed auth.", err=True)
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
