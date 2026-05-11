"""SCIM response models never leak password.

Two AST guards:
  1. The `password` field on `ScimUser` is declared with
     `Field(exclude=True)` so `model_dump()` cannot serialize it.
  2. No `ScimUser(...)` constructor call under `src/postinod/scim/`
     passes `password=...` — write-only field stays write-only.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, iter_source_files

SCIM_ROOT = SRC_ROOT / "postinod" / "scim"


def _scim_files() -> list[Path]:
    return [src.path for src in iter_source_files() if SCIM_ROOT in src.path.parents]


def _find_scim_user_classdef() -> ast.ClassDef:
    models_path = SCIM_ROOT / "models.py"
    tree = ast.parse(models_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ScimUser":
            return node
    raise AssertionError("ScimUser class not found in postinod/scim/models.py")


def _has_field_exclude_true(value: ast.expr) -> bool:
    """Walk an AnnAssign default expr; return True iff it is `Field(... exclude=True ...)`."""
    if not isinstance(value, ast.Call):
        return False
    func = value.func
    if isinstance(func, ast.Name) and func.id != "Field":
        return False
    if isinstance(func, ast.Attribute) and func.attr != "Field":
        return False
    for kw in value.keywords:
        if kw.arg == "exclude" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


def test_scim_user_password_field_is_excluded() -> None:
    cls = _find_scim_user_classdef()
    for stmt in cls.body:
        if (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id == "password"
        ):
            assert stmt.value is not None, (
                "ScimUser.password declared without a Field(...) default; "
                "must be Field(default=None, exclude=True) to satisfy SCIM writeOnly"
            )
            assert _has_field_exclude_true(stmt.value), (
                "ScimUser.password is not Field(exclude=True); SCIM RFC 7643 §7 writeOnly "
                "requires the field to be omitted from response serialization"
            )
            return
    raise AssertionError("ScimUser has no `password` field declaration")


@pytest.mark.parametrize("source_path", _scim_files(), ids=lambda p: p.name)
def test_no_scim_user_constructor_passes_password_kwarg(source_path: Path) -> None:
    """Walk every ScimUser(...) call under postinod/scim/; assert no password kwarg."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "ScimUser":
            for kw in node.keywords:
                if kw.arg == "password":
                    offenders.append(node.lineno)
    assert not offenders, (
        f"{source_path}: ScimUser(...) called with password=... at lines {offenders}. "
        f"SCIM password is write-only — never set it on response models."
    )
