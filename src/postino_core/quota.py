"""Quota size parsing and formatting.

Internal canonical form is `int` bytes. CLI input ("5G") and CLI output
("5.0G") flow through these helpers; everything in between stays int."""

from __future__ import annotations

from postino_core.enums import QuotaUnit
from postino_core.errors import ConfigError

_MULTIPLIERS: dict[QuotaUnit, int] = {
    QuotaUnit.B: 1,
    QuotaUnit.K: 1024,
    QuotaUnit.M: 1024**2,
    QuotaUnit.G: 1024**3,
    QuotaUnit.T: 1024**4,
}


def parse_quota(text: str) -> int:
    """Parse a human quota string into bytes.

    Returns: bytes as int (0 means unlimited).
    Raises: ConfigError on empty input, unknown suffix, or negative value.
    """
    if not text:
        raise ConfigError("quota cannot be empty")

    s = text.strip().upper()
    if s == "0":
        return 0

    suffix = s[-1]
    if suffix.isdigit():
        magnitude = s
        unit = QuotaUnit.B
    else:
        magnitude = s[:-1]
        try:
            unit = QuotaUnit(suffix)
        except ValueError as e:
            raise ConfigError(f"unknown quota suffix: {suffix!r}") from e

    try:
        n = int(magnitude)
    except ValueError as e:
        raise ConfigError(f"invalid quota magnitude: {magnitude!r}") from e

    if n < 0:
        raise ConfigError("quota cannot be negative")

    return n * _MULTIPLIERS[unit]


def format_quota(value: int) -> str:
    """Format byte count into a human string. 0 → 'unlimited'.

    Returns: e.g. '5.0G', '1.5K', '512.0B', 'unlimited'.
    Raises: ConfigError if value is negative.
    """
    if value < 0:
        raise ConfigError("quota cannot be negative")
    if value == 0:
        return "unlimited"

    for unit, mult in (
        (QuotaUnit.T, 1024**4),
        (QuotaUnit.G, 1024**3),
        (QuotaUnit.M, 1024**2),
        (QuotaUnit.K, 1024),
    ):
        if value >= mult:
            return f"{value / mult:.1f}{unit.value}"
    return f"{value}{QuotaUnit.B.value}"
