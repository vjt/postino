"""`postino_core` must remain free of `getpass` imports.

The audit-log `username` column is filled by an injectable actor callable.
CLI callers wire `getpass.getuser`; postinod wires a JWT/Zitadel-derived
resolver. postino_core itself must not depend on getpass — that's a CLI
concern, and importing it would cross the layer boundary documented in
`pyproject.toml` `[tool.importlinter]`.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CORE = REPO / "src" / "postino_core"


def _python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def test_postino_core_does_not_import_getpass() -> None:
    for py in _python_files(CORE):
        text = py.read_text(encoding="utf-8")
        assert "import getpass" not in text, f"{py}: postino_core must not import getpass"
        assert "from getpass" not in text, f"{py}: postino_core must not import getpass"
