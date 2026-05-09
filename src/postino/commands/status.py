"""postino status — daily-ops snapshot. MVP scope: row counts only."""
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from postino_core.services.bundle import ServicesBundle


def _services(ctx: typer.Context) -> ServicesBundle:
    return ctx.obj["services"]  # type: ignore[no-any-return]


def run(ctx: typer.Context) -> None:
    s = _services(ctx)
    md = s.metadata
    counts: list[tuple[str, int]] = []
    with s.engine.connect() as conn:
        for table_name in ("domain", "mailbox", "alias", "quota2"):
            t = md.tables[table_name]
            n = conn.execute(select(func.count()).select_from(t)).scalar_one()
            counts.append((table_name, int(n)))

    console = Console()
    table = Table(title="postino status")
    table.add_column("table")
    table.add_column("rows", justify="right")
    for name, n in counts:
        table.add_row(name, str(n))
    console.print(table)
