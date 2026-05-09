"""Output renderer — Rich tables in human mode, JSON in --json mode."""
from __future__ import annotations

import json
import sys
from typing import Any

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table


class Renderer:
    def __init__(self, *, json: bool, console: Console | None = None) -> None:
        self._json = json
        self._console = console if console is not None else Console()

    def render(self, payload: BaseModel | list[BaseModel]) -> None:
        if self._json:
            self._render_json(payload)
        else:
            self._render_human(payload)

    def _render_json(self, payload: BaseModel | list[BaseModel]) -> None:
        if isinstance(payload, list):
            data: Any = [m.model_dump(mode="json") for m in payload]
        else:
            data = payload.model_dump(mode="json")
        json.dump(data, sys.stdout)
        sys.stdout.write("\n")

    def _render_human(self, payload: BaseModel | list[BaseModel]) -> None:
        items = payload if isinstance(payload, list) else [payload]
        if not items:
            self._console.print("(no rows)")
            return
        first = items[0]
        table = Table(show_header=True, header_style="bold")
        for field in type(first).model_fields:
            table.add_column(field, no_wrap=True)
        for item in items:
            table.add_row(*[
                self._render_cell(getattr(item, f)) for f in type(first).model_fields
            ])
        self._console.print(table)

    def _render_cell(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value)
