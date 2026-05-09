"""Postcreation hook runner."""

from __future__ import annotations

import subprocess
from pathlib import Path

from postino_core.errors import HookError


class HookRunner:
    def __init__(self, *, script_path: Path) -> None:
        self._script_path = script_path

    def run_postcreation(self, username: str) -> None:
        if not self._script_path.exists():
            raise HookError(f"postcreation hook missing: {self._script_path}")
        result = subprocess.run(
            [str(self._script_path), username],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise HookError(
                f"postcreation hook exit {result.returncode}: stderr={result.stderr.strip()!r}"
            )
