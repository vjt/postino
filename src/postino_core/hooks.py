"""Postcreation hook runner."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from postino_core.errors import HookError

_logger = logging.getLogger(__name__)

DEFAULT_HOOK_TIMEOUT_SECONDS = 30.0

# Minimal env passed to the hook subprocess. The parent process's
# environment may contain POSTINO_*, .env-exported credentials,
# PYTHONPATH pointing into a writable venv, etc. — none of which the
# hook needs. Stick to PATH (so the hook can find `chown`, `mkdir`),
# HOME (so any per-user dotfile lookup has somewhere to land), and
# LANG=C (so error messages are predictable). (L2-S26)
_HOOK_ENV: dict[str, str] = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/sbin:/usr/local/bin",
    "HOME": "/",
    "LANG": "C",
}


class HookRunner:
    def __init__(self, *, script_path: Path, timeout: float = DEFAULT_HOOK_TIMEOUT_SECONDS) -> None:
        self._script_path = script_path
        self.timeout = timeout

    def run_postcreation(
        self,
        *,
        username: str,
        domain: str,
        maildir: str,
        quota: int,
    ) -> None:
        """Run the postcreation hook with PostfixAdmin-style positional args.

        Passes four arguments: USERNAME DOMAIN MAILDIR QUOTA — matching the
        PA-style hook contract used in production. The keyword-only signature
        prevents the positional miscount that caused the m42 production failure
        (hook received only USERNAME, DOMAIN and MAILDIR were empty, exit 1).

        Environment is scrubbed to a minimal allowlist before exec; POSTINO_*
        vars and any ``.env``-exported credentials do not leak into the
        hook's env block. The hook contract is positional args only.
        """
        if not self._script_path.exists():
            raise HookError(f"postcreation hook missing: {self._script_path}")
        try:
            result = subprocess.run(
                [str(self._script_path), username, domain, maildir, str(quota)],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=_HOOK_ENV,
                cwd="/",
            )
        except subprocess.TimeoutExpired as e:
            raise HookError(
                f"postcreation hook timed out after {self.timeout}s: {self._script_path}"
            ) from e
        if result.stdout:
            _logger.info(
                "postcreation hook %s stdout: %s", self._script_path, result.stdout.strip()
            )
        if result.stderr:
            _logger.info(
                "postcreation hook %s stderr: %s", self._script_path, result.stderr.strip()
            )
        if result.returncode != 0:
            raise HookError(
                f"postcreation hook exit {result.returncode}: "
                f"stdout={result.stdout.strip()!r} stderr={result.stderr.strip()!r}"
            )
