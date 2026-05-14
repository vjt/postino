"""Architectural layering enforced by ``import-linter``.

Spec: prep action doc PR-A0 §"Architecture tests". The contracts live
in ``pyproject.toml`` under ``[tool.importlinter]``; this test runs
``lint-imports`` and asserts a green report.

Belt-and-braces with the standalone CLI step in ``scripts/check.sh``:
running it inside pytest means the layering rule fails the test suite,
not just the lint step, so editor-driven runs catch violations early.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.architecture
def test_import_linter_contracts_kept() -> None:
    binary = shutil.which("lint-imports")
    if binary is None:
        pytest.skip("lint-imports not installed (pip install -e '.[dev]')")
    completed = subprocess.run(
        [binary],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "import-linter contracts broken:\n"
            f"--- stdout ---\n{completed.stdout}\n"
            f"--- stderr ---\n{completed.stderr}"
        )


@pytest.mark.architecture
def test_routes_table_not_declared_in_source() -> None:
    """routes must be reflected from PA schema, never declared via
    SQLAlchemy `Table(...)` in source. This preserves postino's
    'read PA schema' contract (postino owns the migration DDL only via
    the fixture; live deployments are operator-managed)."""
    src = Path("src/postino_core")
    for py in src.rglob("*.py"):
        text = py.read_text()
        # Permit references to the reflected table by name, but
        # forbid declarative SQLAlchemy Table/CreateTable for routes.
        assert "Table('routes'" not in text and 'Table("routes"' not in text, (
            f"{py} declares routes as SQLAlchemy Table — must reflect instead"
        )
