"""Stub — implemented in Task 25."""
from __future__ import annotations

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command("placeholder")
def _placeholder() -> None:  # pyright: ignore[reportUnusedFunction]
    raise NotImplementedError("quota commands land in Task 25")
