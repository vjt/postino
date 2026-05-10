"""Forbid empty-string defaults on ``str``-typed annotations.

Spec: prep action doc §"Type discipline rules" #5. ``""`` as a sentinel
hides intent — it is impossible to distinguish "user said empty" from
"user said nothing". Replace with ``str | None = None``."""

from __future__ import annotations

import ast
from collections.abc import Iterator

from .conftest import SourceFile, assert_violations_allowlisted, iter_source_files


def _annotation_is_plain_str(annotation: ast.AST | None) -> bool:
    return isinstance(annotation, ast.Name) and annotation.id == "str"


def _is_empty_str(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value == ""


def _function_arg_violations(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    relpath: str,
) -> Iterator[tuple[str, int, str]]:
    """Pair positional/kwonly args with their defaults and flag mismatches."""
    pos_args = list(func.args.posonlyargs) + list(func.args.args)
    pos_defaults = list(func.args.defaults)
    pos_pairs = list(zip(pos_args[-len(pos_defaults) :], pos_defaults, strict=False))
    kw_pairs = [
        (a, d)
        for a, d in zip(func.args.kwonlyargs, func.args.kw_defaults, strict=False)
        if d is not None
    ]
    for arg, default in pos_pairs + kw_pairs:
        if _annotation_is_plain_str(arg.annotation) and _is_empty_str(default):
            yield (
                relpath,
                arg.lineno,
                f"empty-string default on `{arg.arg}: str` — use `str | None = None` instead",
            )


def _ann_assign_violations(
    src: SourceFile,
) -> Iterator[tuple[str, int, str]]:
    for node in ast.walk(src.tree):
        if (
            isinstance(node, ast.AnnAssign)
            and _annotation_is_plain_str(node.annotation)
            and node.value is not None
            and _is_empty_str(node.value)
        ):
            yield (
                src.relpath,
                node.lineno,
                "empty-string default on `: str` — use `str | None = None` instead",
            )


def _violations(src: SourceFile) -> Iterator[tuple[str, int, str]]:
    yield from _ann_assign_violations(src)
    for node in ast.walk(src.tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield from _function_arg_violations(node, src.relpath)


def test_no_empty_string_sentinel_defaults() -> None:
    violations = (v for src in iter_source_files() for v in _violations(src))
    assert_violations_allowlisted("test_no_empty_str_sentinel", violations)
