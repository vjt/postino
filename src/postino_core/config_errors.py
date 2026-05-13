"""Per-file TOML loader + (field-path → file, line) lookup.

Pydantic-settings merges TOML files into a single dict before
validating, which erases which file a given field came from. For
ConfigError messages we want to point operators at the offending
file:line, not just the field name. This module is the side channel
that preserves that origin information.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import tomlkit
from tomlkit.items import Item


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
