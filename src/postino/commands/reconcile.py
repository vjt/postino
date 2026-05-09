"""V2 stub. Always errors with ConfigError."""

from __future__ import annotations

from postino_core.errors import ConfigError


def run() -> None:
    raise ConfigError("reconcile lands in postino V2 (Zitadel sync)")
