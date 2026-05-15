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

from postino.exit import exit_with_error, get_services, is_json, is_no_color, is_quiet
from postino_core.check.consistency import CheckResult, run_consistency_check
from postino_core.errors import ConfigError


def run(
    ctx: typer.Context,
    deep: bool = typer.Option(
        False,
        "--deep",
        help="Reconcile mailbox rows against maildirs on disk and check FK substitutes.",
    ),
) -> None:
    # WHY (v0.12): `postino check` is being renamed to `postino config check`
    # in v0.13. One minor release of stderr-only deprecation warning so
    # ops scripts can be updated without an immediate hard break. JSON
    # output stays on stdout; this print is stderr only.
    if not is_quiet(ctx):
        print(
            "WARN: `postino check` is moving to `postino config check` in v0.13. Update scripts.",
            file=sys.stderr,
        )
    s = get_services(ctx)
    result = run_consistency_check(
        settings=s.settings,
        engine=s.engine,
        metadata=s.metadata,
        deep=deep,
    )
    if is_json(ctx):
        _render_json(result)
    else:
        _render_human(result, no_color=is_no_color(ctx))
    if not result.ok:
        exit_with_error(ConfigError("one or more checks failed"))


def _render_human(result: CheckResult, *, no_color: bool) -> None:
    # Mirror Renderer's dual-arg pattern: color_system=None fully
    # suppresses ANSI (including bold) for script-pipe-safe output.
    console = Console(
        color_system=None if no_color else "auto",
        no_color=no_color,
    )
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
