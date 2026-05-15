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

import typer
from pydantic import SecretStr
from rich.console import Console

from postino_core.config_gen import GenInput, generate
from postino_core.config_gen.templates import registry_names
from postino_core.enums import IdentityBackend
from postino_core.errors import (
    CollisionRefused,
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
    """--db-url > $POSTINO_DB_URL > interactive prompt. No fallback => raise."""
    if flag_url:
        return SecretStr(flag_url)
    env_url = os.environ.get("POSTINO_DB_URL")
    if env_url:
        return SecretStr(env_url)
    if sys.stdin.isatty():
        return SecretStr(typer.prompt("DB URL", hide_input=True))
    raise typer.BadParameter("no --db-url, no POSTINO_DB_URL env var, no TTY — cannot prompt")


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
