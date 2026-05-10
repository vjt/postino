"""Postcreation hook runner."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from postino_core.errors import HookError

_logger = logging.getLogger(__name__)

DEFAULT_HOOK_TIMEOUT_SECONDS = 30.0


class HookRunner:
    def __init__(self, *, script_path: Path, timeout: float = DEFAULT_HOOK_TIMEOUT_SECONDS) -> None:
        self._script_path = script_path
        self.timeout = timeout

    def run_postcreation(self, username: str) -> None:
        if not self._script_path.exists():
            raise HookError(f"postcreation hook missing: {self._script_path}")
        try:
            result = subprocess.run(
                [str(self._script_path), username],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise HookError(
                f"postcreation hook timed out after {self.timeout}s: {self._script_path}"
            ) from e
        if result.stdout:
            _logger.info(
                "postcreation hook %s stdout: %s", self._script_path, result.stdout.strip()
            )
        if result.returncode != 0:
            raise HookError(
                f"postcreation hook exit {result.returncode}: stderr={result.stderr.strip()!r}"
            )
