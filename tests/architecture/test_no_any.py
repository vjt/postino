"""Forbid ``typing.Any`` annotations in production source.

Spec: prep action doc §"Type discipline rules" #1. If a third-party
stub forces ``Any``, wrap it behind a ``Protocol`` in our code.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

from .conftest import SourceFile, assert_violations_allowlisted, iter_source_files


def _resolve_attr(node: ast.AST) -> str | None:
    """Return dotted name for ``ast.Name`` / ``ast.Attribute``, else ``None``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _resolve_attr(node.value)
        if prefix is None:
            return None
        return f"{prefix}.{node.attr}"
    return None


def _annotation_uses_any(node: ast.AST | None) -> bool:
    """``True`` if the annotation references ``Any`` or ``typing.Any``."""
    if node is None:
        return False
    for child in ast.walk(node):
        name = _resolve_attr(child)
        if name in {"Any", "typing.Any", "t.Any"}:
            return True
    return False


def _violations(src: SourceFile) -> Iterator[tuple[str, int, str]]:
    for node in ast.walk(src.tree):
        if isinstance(node, ast.AnnAssign) and _annotation_uses_any(node.annotation):
            yield (src.relpath, node.lineno, "Any annotation on assignment")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in (
                *node.args.args,
                *node.args.kwonlyargs,
                *node.args.posonlyargs,
                node.args.vararg,
                node.args.kwarg,
            ):
                if arg is not None and _annotation_uses_any(arg.annotation):
                    yield (src.relpath, arg.lineno, f"Any annotation on arg '{arg.arg}'")
            if _annotation_uses_any(node.returns):
                yield (src.relpath, node.lineno, f"Any annotation on return of '{node.name}'")


def test_no_any_annotations_in_src() -> None:
    violations = (v for src in iter_source_files() for v in _violations(src))
    assert_violations_allowlisted("test_no_any", violations)
