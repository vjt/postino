"""postino config fix — reconcile a live postfix+dovecot deployment to canonical shape.

Detection: shell out to postconf/doveconf, parse output.
Diff: compare detected dict to canonical target dict, emit human-readable lines + refusals.
Apply: run postconf -e/-X/-Me/-MX, atomic-rename a dovecot fragment file.

No new Pydantic models — detection returns a flat dict[str, str]; refusals
are surfaced as typed exceptions; the renderer (config_gen.generate) owns
the sql cf writes.
"""

from __future__ import annotations

import shutil
import subprocess

from postino_core.errors import FixDetectionFailed


def _which_or_raise(binary: str) -> str:
    path = shutil.which(binary)
    if path is None:
        raise FixDetectionFailed(f"{binary} not on PATH; install postfix/dovecot or fix PATH")
    return path


def _run(argv: list[str]) -> str:
    """Run a read-only detection subprocess; raise FixDetectionFailed on non-zero."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    except OSError as e:
        raise FixDetectionFailed(f"exec {argv[0]} failed: {e}") from e
    if proc.returncode != 0:
        raise FixDetectionFailed(
            f"{argv[0]} exit {proc.returncode}: {proc.stderr.strip() or '(no stderr)'}"
        )
    return proc.stdout


def _postconf_n() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
    """Parse `postconf -n` output. Returns {key: value} for non-default params."""
    out = _run([_which_or_raise("postconf"), "-n"])
    result: dict[str, str] = {}
    for line in out.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, _, val = line.partition("=")
        result[key.strip()] = val.strip()
    return result


def _postconf_d(key: str) -> str:  # pyright: ignore[reportUnusedFunction]
    """Read postfix default-value for a single key. Strips `<key> = ` prefix."""
    out = _run([_which_or_raise("postconf"), "-d", key]).strip()
    _, _, val = out.partition("=")
    return val.strip()


def _doveconf_n() -> str:  # pyright: ignore[reportUnusedFunction]
    """Raw `doveconf -n` text. Block-scoped, so we keep it as text + parse on demand."""
    return _run([_which_or_raise("doveconf"), "-n"])


def _doveconf_h(key: str) -> str:  # pyright: ignore[reportUnusedFunction]
    """`doveconf -h <key>` returns just the value, no key prefix."""
    return _run([_which_or_raise("doveconf"), "-h", key]).strip()
