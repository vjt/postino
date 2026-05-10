"""postinod must remain CLI-free; postino must not import postinod."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def test_postinod_does_not_import_typer_or_rich() -> None:
    for py in _python_files(REPO / "src" / "postinod"):
        text = py.read_text(encoding="utf-8")
        assert "import typer" not in text, f"{py}: postinod must not import typer"
        assert "from typer" not in text, f"{py}: postinod must not import typer"
        assert "import rich" not in text, f"{py}: postinod must not import rich"
        assert "from rich" not in text, f"{py}: postinod must not import rich"


def test_postino_cli_does_not_import_postinod() -> None:
    for py in _python_files(REPO / "src" / "postino"):
        text = py.read_text(encoding="utf-8")
        assert "import postinod" not in text, f"{py}: postino CLI must not import postinod"
        assert "from postinod" not in text, f"{py}: postino CLI must not import postinod"
