"""Console-script entry point.

Pre-processes ``sys.argv`` so a small allow-list of global bool flags
(``--json``, ``--quiet``, ``--no-color``) is accepted at any
position in the command line, not just before the subcommand. Typer
binds options to the level where they're declared and offers no
native opt-in for floating globals, so we shuffle argv ourselves
before handing it off.

This module is the entry point for both ``python -m postino`` and the
installed ``postino`` console script (see ``pyproject.toml`` →
``[project.scripts]``). The shuffle is bool-only by design: if we ever
add a global flag that consumes a value, the helper would need to peek
one token ahead, which is why ``_FLOATING_GLOBALS`` is an explicit set.
"""

from __future__ import annotations

import sys

from postino.cli import app

_FLOATING_GLOBALS: frozenset[str] = frozenset({"--json", "--quiet", "--no-color"})


def _shuffle_globals(argv: list[str], floats: frozenset[str]) -> list[str]:
    """Move occurrences of ``floats`` to the front of argv.

    Preserves order among floats; preserves order among non-floats.
    Idempotent. Operates on a copy.
    """
    moved: list[str] = []
    rest: list[str] = []
    for token in argv:
        if token in floats:
            moved.append(token)
        else:
            rest.append(token)
    return moved + rest


def main() -> None:
    sys.argv = [sys.argv[0], *_shuffle_globals(sys.argv[1:], _FLOATING_GLOBALS)]
    app()


if __name__ == "__main__":
    main()
