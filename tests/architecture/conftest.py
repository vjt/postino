"""Shared helpers for architecture tests.

These tests are static-only — they walk source under ``src/`` and assert
project-wide type/code-discipline rules. They MUST NOT import from
``postino_core`` or ``postino`` because that would tie the rule check to
runtime imports and slow them to a crawl.

Allowlist mechanics
-------------------
Each ``test_no_*.py`` reads a section in ``allowlist.toml`` keyed by the
relative source path. For every reported violation the test demands an
entry ``"<lineno>" = "WHY: ..."`` so the suppression carries its own
justification. When a file is moved, the path key changes and the
suppression is invalidated — forcing re-justification (anti-rot)."""

from __future__ import annotations

import ast
import tomllib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
ALLOWLIST_PATH = Path(__file__).with_name("allowlist.toml")


@dataclass(frozen=True)
class SourceFile:
    """Parsed source file paired with its on-disk text."""

    path: Path
    relpath: str
    source: str
    tree: ast.Module

    def lines(self) -> list[str]:
        return self.source.splitlines()


def iter_source_files() -> Iterator[SourceFile]:
    """Yield every ``*.py`` file under ``src/`` (skipping caches)."""
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        relpath = str(path.relative_to(REPO_ROOT))
        yield SourceFile(
            path=path,
            relpath=relpath,
            source=text,
            tree=ast.parse(text, filename=str(path)),
        )


def load_allowlist() -> dict[str, dict[str, dict[str, str]]]:
    """Return the parsed allowlist or an empty mapping if absent.

    Layout: ``{ "<test_id>": { "<relpath>": { "<lineno>": "WHY: ..." } } }``.
    """
    if not ALLOWLIST_PATH.exists():
        return {}
    raw: dict[str, Any] = tomllib.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    typed: dict[str, dict[str, dict[str, str]]] = {}
    for test_id, files in raw.items():
        if not isinstance(files, dict):
            continue
        files_typed: dict[str, dict[str, str]] = {}
        for relpath, lines in cast(dict[str, Any], files).items():
            if not isinstance(lines, dict):
                continue
            files_typed[relpath] = {str(k): str(v) for k, v in cast(dict[str, Any], lines).items()}
        typed[test_id] = files_typed
    return typed


def allowed_for(test_id: str, relpath: str, lineno: int) -> str | None:
    """Return the justification for a (test, file, line) suppression.

    None means *not allowed*; the test must fail.
    """
    table = load_allowlist().get(test_id, {}).get(relpath, {})
    return table.get(str(lineno))


def assert_violations_allowlisted(
    test_id: str,
    violations: Iterable[tuple[str, int, str]],
) -> None:
    """Fail the test if any violation lacks an allowlist entry.

    ``violations`` is an iterable of ``(relpath, lineno, message)``.
    """
    unjustified: list[str] = []
    for relpath, lineno, message in violations:
        why = allowed_for(test_id, relpath, lineno)
        if why is None:
            unjustified.append(f"{relpath}:{lineno}: {message}")
        elif not why.strip().upper().startswith("WHY:"):
            unjustified.append(
                f"{relpath}:{lineno}: allowlist entry must start with 'WHY:' (got {why!r})"
            )
    if unjustified:
        joined = "\n  ".join(unjustified)
        raise AssertionError(
            f"Architecture rule '{test_id}' has unjustified violations:\n  {joined}\n"
            f'Either fix them, or add an entry under [{test_id}."<relpath>"] in '
            f'{ALLOWLIST_PATH.name} as <lineno> = "WHY: <reason>".'
        )
