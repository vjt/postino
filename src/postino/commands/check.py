"""postino check — print findings + exit non-zero on errors.

Exit codes:

* 0 — every finding is severity="info".
* 4 — at least one finding is severity="error" (ConfigError).

Warnings (severity="warn") are surfaced visually but do not flip exit
code; that's the contract for non-blocking issues.
"""

from __future__ import annotations

import json as _json
import sys

import typer
from rich.console import Console

from postino_core.check.consistency import CheckResult, run_consistency_check
from postino_core.services.bundle import ServicesBundle


def _services(ctx: typer.Context) -> ServicesBundle:
    return ctx.obj["services"]  # type: ignore[no-any-return]  # WHY: typer Context.obj is dict[str, Any]; PR-A6 typed CliState.


def run(
    ctx: typer.Context,
    deep: bool = typer.Option(
        False,
        "--deep",
        help="Reconcile mailbox rows against maildirs on disk and check FK substitutes.",
    ),
) -> None:
    from postino.cli import exit_with_error as _exit
    from postino_core.errors import ConfigError

    s = _services(ctx)
    json_mode = bool(ctx.obj["json"])
    result = run_consistency_check(
        settings=s.settings,
        engine=s.engine,
        metadata=s.metadata,
        deep=deep,
    )
    if json_mode:
        _render_json(result)
    else:
        _render_human(result)
    if not result.ok:
        _exit(ConfigError("one or more checks failed"))


def _render_human(result: CheckResult) -> None:
    console = Console()
    marks = {
        "info": "[green]✓[/green]",
        "warn": "[yellow]![/yellow]",
        "error": "[red]✗[/red]",
    }
    for f in result.findings:
        console.print(f"{marks[f.severity]} {f.name}: {f.message}")


def _render_json(result: CheckResult) -> None:
    _json.dump(result.model_dump(mode="json"), sys.stdout)
    sys.stdout.write("\n")
