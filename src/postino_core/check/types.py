"""Leaf types for ``postino_core.check``.

Lives in its own module so ``postino_core.errors`` can type the
structured payloads it carries (``PreflightFailed.findings``,
``PostCheckFailed.findings``) without importing ``consistency.py`` —
which itself depends on ``errors.ConfigError`` and would create a
cycle. Anything that wants to type a ``Finding`` should import from
here; ``consistency`` re-exports ``Finding`` and ``Severity`` for the
existing call sites.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Severity = Literal["info", "warn", "error"]


class Finding(BaseModel):
    """One row in a ``postino check`` report."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str
    severity: Severity
    message: str

    @property
    def ok(self) -> bool:
        return self.severity == "info"
