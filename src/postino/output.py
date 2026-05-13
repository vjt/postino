"""Output renderer — Rich tables in human mode, JSON in --json mode.

Lives in the CLI layer (``postino``) — Rich and JSON-formatted CLI output
have no place inside ``postino_core``, which must stay free of UI deps.
The import-linter contract enforces that.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    import typer


class Renderer:
    def __init__(
        self,
        *,
        json: bool,
        quiet: bool = False,
        no_color: bool = False,
        console: Console | None = None,
    ) -> None:
        self._json = json
        self._quiet = quiet
        self._no_color = no_color
        if console is not None:
            self._console = console
        else:
            # color_system=None disables ALL ANSI emission (including bold/
            # reset codes). Rich's no_color=True only suppresses foreground
            # colors — the bold-header markup in our tables would still
            # leak \x1b[1m/\x1b[0m into piped output, defeating the flag's
            # purpose. color_system=None is the right hammer here.
            self._console = Console(
                color_system=None if no_color else "auto",
                no_color=no_color,
            )

    @classmethod
    def from_ctx(cls, ctx: typer.Context) -> Renderer:
        """Build a Renderer reading json/quiet/no_color from CliState.

        Sweep target: replaces ``Renderer(json=is_json(ctx))`` at command
        sites so the three flags arrive at the renderer without three
        ``is_X(ctx)`` calls at every callsite.
        """
        # Local import to avoid an output.py → typer/exit.py runtime cycle
        # (output.py is otherwise typer-free; TYPE_CHECKING import handles
        # the ctx type annotation).
        from postino.exit import is_json, is_no_color, is_quiet

        return cls(
            json=is_json(ctx),
            quiet=is_quiet(ctx),
            no_color=is_no_color(ctx),
        )

    def render(self, payload: BaseModel | Sequence[BaseModel]) -> None:
        if self._json:
            self._render_json(payload)
        else:
            self._render_human(payload)

    def _render_json(self, payload: BaseModel | Sequence[BaseModel]) -> None:
        if isinstance(payload, BaseModel):
            json.dump(payload.model_dump(mode="json"), sys.stdout)
        else:
            data = [m.model_dump(mode="json") for m in payload]
            json.dump(data, sys.stdout)
        sys.stdout.write("\n")

    def _render_human(self, payload: BaseModel | Sequence[BaseModel]) -> None:
        items: Sequence[BaseModel] = (payload,) if isinstance(payload, BaseModel) else payload
        if not items:
            self._console.print("(no rows)")
            return
        first = items[0]
        table = Table(show_header=True, header_style="bold")
        for field in type(first).model_fields:
            table.add_column(field, no_wrap=True)
        for item in items:
            table.add_row(*[self._render_cell(getattr(item, f)) for f in type(first).model_fields])
        self._console.print(table)

    def _render_cell(self, value: object) -> str:
        if value is None:
            return ""
        return str(value)
