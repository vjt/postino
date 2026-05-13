"""Per-file TOML loader + (field-path → file, line) lookup.

Pydantic-settings merges TOML files into a single dict before
validating, which erases which file a given field came from. For
ConfigError messages we want to point operators at the offending
file:line, not just the field name. This module is the side channel
that preserves that origin information.
"""

from __future__ import annotations

import tomllib
from pathlib import Path


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
