"""``python -m postino`` entrypoint.

Mirrors the ``postino`` console script registered in ``pyproject.toml``
so callers can invoke the CLI without relying on the script being on
PATH (useful for bind-mount-over-PYTHONPATH layouts where the installed
script is shadowed by source code).
"""

from __future__ import annotations

from postino.cli import app

if __name__ == "__main__":
    app()
