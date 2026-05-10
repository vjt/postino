"""Forbid bare ``dict`` and ``dict[..., Any]`` in function signatures.

Spec: prep action doc §"Type discipline rules" #6. Public handlers and
services pass Pydantic models, never raw dicts. ``dict[str, str]`` (and
other concrete value types) are still fine — only the ``Any``-typed and
bare ``dict`` forms break the contract."""

from __future__ import annotations

import ast
from collections.abc import Iterator

from .conftest import SourceFile, assert_violations_allowlisted, iter_source_files


def _is_dict_name(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "dict"


def _is_any_node(node: ast.AST) -> bool:
    if isinstance(node, ast.Name) and node.id == "Any":
        return True
    return isinstance(node, ast.Attribute) and node.attr == "Any"


def _annotation_violations(annotation: ast.AST | None) -> list[str]:
    """Return human-readable violation messages for an annotation subtree."""
    if annotation is None:
        return []
    findings: list[str] = []
    for node in ast.walk(annotation):
        if _is_dict_name(node):
            findings.append("bare `dict` annotation")
        elif isinstance(node, ast.Subscript) and _is_dict_name(node.value):
            slc = node.slice
            elements: list[ast.AST] = list(slc.elts) if isinstance(slc, ast.Tuple) else [slc]
            if any(_is_any_node(e) for e in elements):
                findings.append("`dict[..., Any]` annotation")
    return findings


def _violations(src: SourceFile) -> Iterator[tuple[str, int, str]]:
    for node in ast.walk(src.tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in (
                *node.args.args,
                *node.args.kwonlyargs,
                *node.args.posonlyargs,
                node.args.vararg,
                node.args.kwarg,
            ):
                if arg is None:
                    continue
                for msg in _annotation_violations(arg.annotation):
                    yield (src.relpath, arg.lineno, f"{msg} on arg '{arg.arg}'")
            for msg in _annotation_violations(node.returns):
                yield (src.relpath, node.lineno, f"{msg} on return of '{node.name}'")


def test_no_bare_dict_in_signatures() -> None:
    violations = (v for src in iter_source_files() for v in _violations(src))
    assert_violations_allowlisted("test_no_bare_dict", violations)
