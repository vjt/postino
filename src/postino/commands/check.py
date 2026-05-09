"""postino check — print human findings + exit 0 if all green, else exit 4."""
from __future__ import annotations

import typer
from rich.console import Console

from postino_core.check.consistency import run_consistency_check
from postino_core.services.bundle import ServicesBundle


def _services(ctx: typer.Context) -> ServicesBundle:
    return ctx.obj["services"]  # type: ignore[no-any-return]


def run(ctx: typer.Context) -> None:
    from postino.cli import exit_with_error as _exit
    from postino_core.errors import ConfigError

    s = _services(ctx)
    result = run_consistency_check(
        settings=s.settings, engine=s.engine, metadata=s.metadata,
    )
    console = Console()
    for f in result.findings:
        mark = "[green]✓[/green]" if f.ok else "[red]✗[/red]"
        console.print(f"{mark} {f.name}: {f.message}")
    if not result.ok:
        _exit(ConfigError("one or more checks failed"))
