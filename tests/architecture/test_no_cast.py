"""Forbid ``typing.cast`` calls in production source.

Spec: prep action doc §"Type discipline rules" #2. ``cast`` lies to the
type checker — fix the underlying type instead.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

from .conftest import SourceFile, assert_violations_allowlisted, iter_source_files


def _is_cast_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id == "cast":
        return True
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "cast"
        and isinstance(func.value, ast.Name)
        and func.value.id in {"typing", "t"}
    )


def _violations(src: SourceFile) -> Iterator[tuple[str, int, str]]:
    for node in ast.walk(src.tree):
        if isinstance(node, ast.Call) and _is_cast_call(node):
            yield (src.relpath, node.lineno, "typing.cast() call")


def test_no_typing_cast_in_src() -> None:
    violations = (v for src in iter_source_files() for v in _violations(src))
    assert_violations_allowlisted("test_no_cast", violations)
