"""postino status — daily-ops snapshot. MVP scope: row counts only."""

from __future__ import annotations

import typer

from postino.exit import get_services
from postino.output import Renderer


def run(ctx: typer.Context) -> None:
    s = get_services(ctx)
    Renderer.from_ctx(ctx).render(s.status.snapshot())
