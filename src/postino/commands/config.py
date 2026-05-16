"""Typer group `postino config` — config artifact generation.

`postino check` stays at the top level as a deprecated alias for one
minor release; the deprecation warning is printed inside
``postino.commands.check.run`` itself (Typer reflects on the wrapped
callable's signature, so a thin wrapper here would lose the Click
options).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated
from urllib.parse import quote_plus

import typer
from pydantic import SecretStr
from rich.console import Console

from postino_core.config_gen import GenInput, generate
from postino_core.config_gen import fix as fix_module
from postino_core.config_gen.generator import (
    _build_context,  # pyright: ignore[reportPrivateUsage]  # WHY: config fix renders the dovecot fragment via the same context builder as config gen.
)
from postino_core.config_gen.templates import registry_names
from postino_core.enums import IdentityBackend
from postino_core.errors import (
    CollisionRefused,
    FixAmbiguity,
    FixApplyError,
    FixDetectionFailed,
    FixDovecotConflict,
    PostCheckFailed,
    PreflightFailed,
    RenderError,
)

app = typer.Typer(no_args_is_help=True)

# WHY: `Annotated[T, typer.Option(...)] = literal` keeps the default a
# plain literal (ruff B008 hates `Path(...)` in defaults). Path defaults
# are constructed inside the body when needed.
_DEFAULT_OUT = "./postino-cfg"
_DEFAULT_MLMMJ_SPOOL = "/var/spool/mlmmj"
_DEFAULT_POSTFIX_DIR = "/etc/postfix"
_DEFAULT_DOVECOT_DIR = "/etc/dovecot"


def _resolve_db_url(flag_url: str | None) -> SecretStr:
    """--db-url > $POSTINO_DB_URL > interactive field prompts. No fallback => raise.

    Interactive path asks host/user/password/dbname separately (password hidden);
    we assemble the mysql+pymysql:// URL ourselves so operators don't have to know
    the SQLAlchemy URL grammar or escape special chars in passwords.
    """
    if flag_url:
        return SecretStr(flag_url)
    env_url = os.environ.get("POSTINO_DB_URL")
    if env_url:
        return SecretStr(env_url)
    if not sys.stdin.isatty():
        raise typer.BadParameter("no --db-url, no POSTINO_DB_URL env var, no TTY — cannot prompt")
    host = typer.prompt("DB host", default="localhost")
    port = typer.prompt("DB port", default=3306, type=int)
    user = typer.prompt("DB user")
    password = typer.prompt("DB password", hide_input=True)
    dbname = typer.prompt("DB name", default="postfix")
    return SecretStr(
        f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{dbname}"
    )


@app.command("gen")
def gen(
    identity_backend: Annotated[
        IdentityBackend,
        typer.Option("--identity-backend", help="local | noauth | hybrid"),
    ],
    out: Annotated[str, typer.Option("--out")] = _DEFAULT_OUT,
    db_url: Annotated[str | None, typer.Option("--db-url")] = None,
    in_place: Annotated[bool, typer.Option("--in-place")] = False,
    mlmmj_spool: Annotated[str, typer.Option("--mlmmj-spool")] = _DEFAULT_MLMMJ_SPOOL,
    mlmmj_uid: Annotated[str, typer.Option("--mlmmj-uid")] = "mlmmj",
    mlmmj_gid: Annotated[str, typer.Option("--mlmmj-gid")] = "mlmmj",
    vmail_uid: Annotated[int, typer.Option("--vmail-uid")] = 5000,
    vmail_gid: Annotated[int, typer.Option("--vmail-gid")] = 5000,
    postfix_dir: Annotated[str, typer.Option("--postfix-dir")] = _DEFAULT_POSTFIX_DIR,
    dovecot_dir: Annotated[str, typer.Option("--dovecot-dir")] = _DEFAULT_DOVECOT_DIR,
    only: Annotated[str | None, typer.Option("--only", help="comma-separated")] = None,
    skip: Annotated[str | None, typer.Option("--skip", help="comma-separated")] = None,
    no_preflight: Annotated[bool, typer.Option("--no-preflight")] = False,
    no_postcheck: Annotated[bool, typer.Option("--no-postcheck")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Emit canonical postfix + dovecot config artifacts."""
    out_path = Path(out)
    mlmmj_spool_path = Path(mlmmj_spool)
    postfix_dir_path = Path(postfix_dir)
    dovecot_dir_path = Path(dovecot_dir)
    console = Console()
    valid_names = registry_names()
    only_set: frozenset[str] = frozenset(only.split(",")) if only else frozenset()
    skip_set: frozenset[str] = frozenset(skip.split(",")) if skip else frozenset()
    for s in only_set | skip_set:
        if s not in valid_names:
            raise typer.BadParameter(f"unknown renderer {s!r}; valid: {sorted(valid_names)}")

    try:
        url = _resolve_db_url(db_url)
    except typer.BadParameter as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1) from e

    input_model = GenInput(
        db_url=url,
        identity_backend=identity_backend,
        mlmmj_spool_dir=mlmmj_spool_path,
        mlmmj_uid=mlmmj_uid,
        mlmmj_gid=mlmmj_gid,
        vmail_uid=vmail_uid,
        vmail_gid=vmail_gid,
        postfix_dir=postfix_dir_path,
        dovecot_dir=dovecot_dir_path,
        in_place=in_place,
        skip_preflight=no_preflight,
        skip_postcheck=no_postcheck,
        only=only_set,
        skip=skip_set,
    )

    if dry_run:
        # WHY: dry-run needs the DB-derived context but cannot call
        # generate() (which writes). _build_context is module-private
        # by convention; the CLI is the one place that legitimately
        # needs it. Test suite mocks the same symbol path.
        from postino_core.config_gen.generator import (
            _build_context,  # pyright: ignore[reportPrivateUsage]  # WHY: dry-run reuses the same context builder as the write path; see config_gen design spec.
        )
        from postino_core.config_gen.templates import render_all

        ctx = _build_context(input_model)
        results = render_all(ctx, only=only_set, skip=skip_set)
        console.print(f"Would emit {len(results)} files into {out_path}:")
        for r in results:
            console.print(f"  • {r.rel_path}  (mode {oct(r.mode)})")
        return

    try:
        result = generate(input_model, out_path)
    except PreflightFailed as e:
        console.print("[red]✗ preflight refused:[/red]")
        for f in e.findings:
            console.print(f"  • {f}")
        raise typer.Exit(1) from e
    except CollisionRefused as e:
        console.print(f"[red]✗ collision:[/red] {e}")
        console.print("  Pass --in-place to overwrite.")
        raise typer.Exit(2) from e
    except RenderError as e:
        console.print(f"[red]✗ render error:[/red] {e}")
        raise typer.Exit(3) from e
    except PostCheckFailed as e:
        console.print("[red]✗ post-emit check failed:[/red]")
        for f in e.findings:
            console.print(f"  • {f}")
        raise typer.Exit(4) from e
    except OSError as e:
        console.print(f"[red]✗ IO error:[/red] {e}")
        raise typer.Exit(5) from e

    console.print(f"[green]✓[/green] wrote {len(result.written)} files to {out_path}")


@app.command("fix")
def fix(
    apply: Annotated[
        bool, typer.Option("--apply", help="Apply the diff. Default is dry-run.")
    ] = False,
    vmail_uid: Annotated[int | None, typer.Option("--vmail-uid")] = None,
    vmail_gid: Annotated[int | None, typer.Option("--vmail-gid")] = None,
    virtual_mailbox_base: Annotated[
        str | None,
        typer.Option("--virtual-mailbox-base", help="Override detected vmail base."),
    ] = None,
    db_url: Annotated[str | None, typer.Option("--db-url")] = None,
) -> None:
    """Reconcile live postfix+dovecot config to canonical postino shape."""
    from postino.cli import (
        _load_settings,  # pyright: ignore[reportPrivateUsage]  # WHY: config fix needs the same PostinoSettings loader; importing at module top would create a cycle.
    )

    console = Console()
    try:
        detected = fix_module.detect()
    except FixDetectionFailed as e:
        console.print(f"[red]✗ detection failed:[/red] {e}")
        raise typer.Exit(1) from e

    try:
        eff_uid, eff_gid = fix_module.effective_vmail(
            detected,
            override_uid=vmail_uid,
            override_gid=vmail_gid,
        )
    except FixAmbiguity as e:
        console.print(f"[red]✗ ambiguity:[/red] {e}")
        raise typer.Exit(2) from e

    settings = _load_settings()
    base_override = virtual_mailbox_base or detected.get("virtual_mailbox_base", "")
    mlmmj_on = settings.mlmmj_spool_dir is not None

    target = fix_module.build_target_postfix(
        postfix_dir=detected.get("postfix.config_dir", "/etc/postfix"),
        lmtp_socket=settings.lmtp_destination.removeprefix("unix:"),
        mlmmj_on=mlmmj_on,
    )

    console.print(
        f"postino config fix — detected {detected.get('postfix.config_dir', '?')}, "
        f"{detected.get('dovecot.etc_dir', '?')}"
    )
    console.print(f"mlmmj: {'ON' if mlmmj_on else 'OFF'}")
    console.print(f"effective vmail: uid={eff_uid} gid={eff_gid} base={base_override}")
    console.print("")
    for line in fix_module.diff(detected, target, mlmmj_target_on=mlmmj_on):
        console.print(line)

    if not apply:
        console.print("\nTo apply: postino config fix --apply")
        return

    # Surface dovecot conflicts BEFORE we open a DB connection — otherwise a
    # passdb-overlap refusal would be masked by a DB connect failure on hosts
    # where the operator hasn't yet provided creds.
    if detected.get("dovecot.has_sql_passdb") == "true":
        console.print("[red]✗ dovecot conflict:[/red] dovecot already has passdb { driver = sql }")
        raise typer.Exit(3)
    if detected.get("dovecot.has_sql_userdb") == "true":
        console.print("[red]✗ dovecot conflict:[/red] dovecot already has userdb { driver = sql }")
        raise typer.Exit(3)
    if detected.get("dovecot.has_lmtp_listener") == "true":
        console.print(
            "[red]✗ dovecot conflict:[/red] dovecot already has "
            "service lmtp { unix_listener private/dovecot-lmtp }"
        )
        raise typer.Exit(3)

    try:
        url = _resolve_db_url(db_url)
    except typer.BadParameter as e:
        console.print(f"[red]✗ db url:[/red] {e}")
        raise typer.Exit(1) from e

    gen_input = GenInput(
        db_url=url,
        identity_backend=settings.identity_backend,
        mlmmj_spool_dir=settings.mlmmj_spool_dir,
        vmail_uid=eff_uid,
        vmail_gid=eff_gid,
        virtual_mailbox_base=Path(base_override),
        postfix_dir=Path(detected.get("postfix.config_dir", "/etc/postfix")),
        dovecot_dir=Path(detected.get("dovecot.etc_dir", "/etc/dovecot")),
        in_place=True,
    )
    fragment_text = fix_module.render_fragment(_build_context(gen_input))
    fragment_path = Path(detected.get("dovecot.etc_dir", "/etc/dovecot")) / "dovecot-postino.conf"

    try:
        fix_module.apply(
            detected,
            target,
            mlmmj_target_on=mlmmj_on,
            gen_input=gen_input,
            out_dir=Path(detected.get("postfix.config_dir", "/etc/postfix")),
            dovecot_fragment_path=fragment_path,
            fragment_content=fragment_text,
        )
    except FixAmbiguity as e:
        console.print(f"[red]✗ ambiguity:[/red] {e}")
        raise typer.Exit(2) from e
    except FixDovecotConflict as e:
        console.print(f"[red]✗ dovecot conflict:[/red] {e}")
        raise typer.Exit(3) from e
    except FixApplyError as e:
        console.print(f"[red]✗ apply failed:[/red] {e}")
        raise typer.Exit(5) from e
    except OSError as e:
        console.print(f"[red]✗ IO error:[/red] {e}")
        raise typer.Exit(5) from e

    console.print(
        f"\n[green]✓[/green] applied. Now run:\n"
        f"  postfix reload\n"
        f"  echo '!include {fragment_path}' >> {fragment_path.parent}/dovecot.conf  "
        f"# if not already\n"
        f"  dovecot reload"
    )
