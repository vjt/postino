"""Forbid string-keyed dispatch tables.

Spec: prep action doc §"Type discipline rules" #4. Use ``enum.Enum`` or
``typing.Literal`` instead of ``dict[str, Callable[...]]`` so the
selector is closed and exhaustively typed.

Detection: an ``AnnAssign`` whose annotation is ``dict[str, ...]`` and
whose value-type contains ``Callable``."""

from __future__ import annotations

import ast
from collections.abc import Iterator

from .conftest import SourceFile, assert_violations_allowlisted, iter_source_files


def _is_dict_subscript(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "dict"
    )


def _annotation_is_str_callable_dispatch(annotation: ast.AST) -> bool:
    if not _is_dict_subscript(annotation):
        return False
    assert isinstance(annotation, ast.Subscript)
    slc = annotation.slice
    if not isinstance(slc, ast.Tuple) or len(slc.elts) != 2:
        return False
    key, value = slc.elts
    if not (isinstance(key, ast.Name) and key.id == "str"):
        return False
    for inner in ast.walk(value):
        if isinstance(inner, ast.Name) and inner.id == "Callable":
            return True
        if isinstance(inner, ast.Attribute) and inner.attr == "Callable":
            return True
    return False


def _violations(src: SourceFile) -> Iterator[tuple[str, int, str]]:
    for node in ast.walk(src.tree):
        if isinstance(node, ast.AnnAssign) and _annotation_is_str_callable_dispatch(
            node.annotation
        ):
            yield (
                src.relpath,
                node.lineno,
                "string-keyed Callable dispatch table — use Enum or Literal keys",
            )


def test_no_str_keyed_callable_dispatch() -> None:
    violations = (v for src in iter_source_files() for v in _violations(src))
    assert_violations_allowlisted("test_no_str_dispatch", violations)
