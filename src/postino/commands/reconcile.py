"""V2 stub.

Always exits 4 (ConfigError-shaped) with a clear message; the actual
reconcile work lands with postinod (IdP-driven user lifecycle).
"""

from __future__ import annotations

import typer


def run(ctx: typer.Context) -> None:
    del ctx  # registered under @app.callback like every other verb; services unused.
    typer.echo(
        "error: reconcile lands in postino V2 (IdP-driven sync via postinod)",
        err=True,
    )
    raise typer.Exit(code=4)
