"""Stub — implemented in Task 23."""
from __future__ import annotations

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command("placeholder")
def _placeholder() -> None:  # pyright: ignore[reportUnusedFunction]
    raise NotImplementedError("alias commands land in Task 23")
