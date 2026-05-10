"""postino status — daily-ops snapshot. MVP scope: row counts only."""

from __future__ import annotations

import typer

from postino.output import Renderer
from postino_core.services.bundle import ServicesBundle


def _services(ctx: typer.Context) -> ServicesBundle:
    return ctx.obj["services"]  # type: ignore[no-any-return]  # WHY: typer Context.obj is dict[str, Any]; PR-A6 typed CliState.


def run(ctx: typer.Context) -> None:
    s = _services(ctx)
    renderer = Renderer(json=bool(ctx.obj["json"]))
    renderer.render(s.status.snapshot())
