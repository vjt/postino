"""postino status — daily-ops snapshot. MVP scope: row counts only."""

from __future__ import annotations

import typer

from postino.exit import get_services, is_json
from postino.output import Renderer


def run(ctx: typer.Context) -> None:
    s = get_services(ctx)
    Renderer(json=is_json(ctx)).render(s.status.snapshot())
