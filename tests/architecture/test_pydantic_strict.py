"""Every Pydantic ``BaseModel`` declares ``strict=True, extra="forbid"``.

Spec: prep action doc §"Type discipline rules" #7. Strict mode keeps
boundary types from silently coercing; ``extra="forbid"`` catches typo'd
config keys.

``BaseSettings`` subclasses are exempt from ``strict=True`` because env
vars come in as strings and need pydantic's coercion to land as int /
bool / Path. ``extra="forbid"`` still required so unknown env vars
fail loud.

``frozen=True`` is recommended where applicable but enforced separately
by code review (some models must remain mutable, e.g. clock-injected
service inputs)."""

from __future__ import annotations

import ast
from collections.abc import Iterator

from .conftest import SourceFile, assert_violations_allowlisted, iter_source_files

_BASE_MODEL_NAMES = {"BaseModel"}
_BASE_SETTINGS_NAMES = {"BaseSettings"}
_CONFIG_DICT_NAMES = {"ConfigDict", "SettingsConfigDict"}


def _bases_named(cls: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for base in cls.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


def _find_model_config_call(cls: ast.ClassDef) -> ast.Call | None:
    """Return the ``model_config = ConfigDict(...)`` Call node, or None."""
    for stmt in cls.body:
        targets: list[ast.AST]
        value: ast.AST | None
        if isinstance(stmt, ast.Assign):
            targets = list(stmt.targets)
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            targets = [stmt.target]
            value = stmt.value
        else:
            continue
        for target in targets:
            if not (isinstance(target, ast.Name) and target.id == "model_config"):
                continue
            if not isinstance(value, ast.Call):
                continue
            func = value.func
            if isinstance(func, ast.Name) and func.id in _CONFIG_DICT_NAMES:
                return value
            if isinstance(func, ast.Attribute) and func.attr in _CONFIG_DICT_NAMES:
                return value
    return None


def _kwarg_value(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _is_true(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _is_str_literal(node: ast.expr | None, expected: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == expected


def _violations_for_class(cls: ast.ClassDef, relpath: str) -> Iterator[tuple[str, int, str]]:
    bases = _bases_named(cls)
    is_base_model = bool(bases & _BASE_MODEL_NAMES)
    is_base_settings = bool(bases & _BASE_SETTINGS_NAMES)
    if not (is_base_model or is_base_settings):
        return
    config = _find_model_config_call(cls)
    if config is None:
        yield (
            relpath,
            cls.lineno,
            f"`{cls.name}` is a Pydantic model but declares no `model_config`",
        )
        return
    extra = _kwarg_value(config, "extra")
    if not _is_str_literal(extra, "forbid"):
        yield (
            relpath,
            cls.lineno,
            f'`{cls.name}` model_config missing `extra="forbid"`',
        )
    if is_base_model and not is_base_settings:
        strict = _kwarg_value(config, "strict")
        if not _is_true(strict):
            yield (
                relpath,
                cls.lineno,
                f"`{cls.name}` model_config missing `strict=True`",
            )


def _violations(src: SourceFile) -> Iterator[tuple[str, int, str]]:
    for node in ast.walk(src.tree):
        if isinstance(node, ast.ClassDef):
            yield from _violations_for_class(node, src.relpath)


def test_pydantic_models_use_strict_and_forbid_extra() -> None:
    violations = (v for src in iter_source_files() for v in _violations(src))
    assert_violations_allowlisted("test_pydantic_strict", violations)
