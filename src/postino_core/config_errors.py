"""Per-file TOML loader + (field-path → file, line) lookup.

Pydantic-settings merges TOML files into a single dict before
validating, which erases which file a given field came from. For
ConfigError messages we want to point operators at the offending
file:line, not just the field name. This module is the side channel
that preserves that origin information.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import tomlkit
from pydantic import ValidationError
from tomlkit.items import Item

_MAX_ERRORS = 5


def _quote(value: object) -> str:
    """Render a config value back to a TOML-ish literal for error messages.

    JSON output matches the TOML source the operator is staring at
    (double-quoted strings, lowercase ``true``/``false``/``null``,
    plain numbers, bracketed lists) better than Python's ``repr``
    would. Non-JSON-native types fall back to ``repr`` via ``default``.
    """
    return json.dumps(value, default=repr)


def load_toml_with_origin(paths: list[Path]) -> list[tuple[Path, dict[str, object]]]:
    """Load each TOML file that exists; return [(path, dict)] in the
    same order as input. Missing files are silently skipped.

    The dict has subtables (e.g. ``[postinod]``) stripped out the same
    way ``postino_core.config`` strips them, so the leaf-key lookup is
    consistent with what pydantic-settings sees.
    """
    out: list[tuple[Path, dict[str, object]]] = []
    for path in paths:
        if not path.is_file():
            continue
        with path.open("rb") as fp:
            raw = tomllib.load(fp)
        leaf: dict[str, object] = {
            k: v
            for k, v in raw.items()  # type: ignore[union-attr]  # WHY: tomllib stubs type load() as dict[str, Any]; iterating top-level dict is safe — same pattern as postino_core.config._TomlSettingsSource.__call__.
            if not isinstance(v, dict)
        }
        out.append((path, leaf))
    return out


def field_origin(path: Path, key: str) -> tuple[Path, int, object] | None:
    """Return (path, 1-based line, raw value) for a top-level ``key``.

    Returns ``None`` when the key isn't present in the file.
    """
    text = path.read_text()
    doc = tomlkit.parse(text)
    if key not in doc:
        return None
    item = doc[key]
    line = _line_for_key(text, key)
    raw: object = item.unwrap() if isinstance(item, Item) else item
    return (path, line, raw)


def format_validation_error(
    error: ValidationError,
    sources: list[tuple[Path, dict[str, object]]],
) -> str:
    """Format a pydantic ValidationError with TOML file:line context.

    For each error, look up its top-level key in the source list (first
    match wins) and prepend ``file:line: key`` to the pydantic message.
    Errors whose key isn't in any TOML (env-set or default) get a
    fallback header so we don't pretend they came from a file.
    """
    errors = error.errors()
    lines: list[str] = [f"{len(errors)} config error{'s' if len(errors) != 1 else ''}:"]

    by_key: dict[str, tuple[Path, int, object]] = {}
    for path, contents in sources:
        for key in contents:
            origin = field_origin(path, key)
            if origin is not None and key not in by_key:
                by_key[key] = origin

    for err in errors[:_MAX_ERRORS]:
        loc = err["loc"]
        key = str(loc[0]) if loc else "<root>"
        msg = err["msg"]
        origin = by_key.get(key)
        if origin is not None:
            file_, line, value = origin
            lines.append(f"  {file_}:{line}: {key}")
            lines.append(f"    {msg} (got {_quote(value)})")
        else:
            lines.append(f"  (no file — env var or default): {key}")
            lines.append(f"    {msg}")

    overflow = len(errors) - _MAX_ERRORS
    if overflow > 0:
        lines.append(f"  (and {overflow} more — fix these and re-run)")

    return "\n".join(lines)


def _line_for_key(text: str, key: str) -> int:
    """1-based line of the first ``key = ...`` assignment.

    tomlkit's Item doesn't expose a public source position in 0.13;
    a regex scan is reliable enough for top-level keys (subtables
    are stripped before validation).
    """
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=", re.MULTILINE)
    m = pattern.search(text)
    if m is None:
        return 0
    return text.count("\n", 0, m.start()) + 1
