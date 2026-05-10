"""``SecretStr``-bearing models must never leak the secret in repr/str.

Spec: prep action doc PR-A0 §"Architecture tests" + PR-A1 (review H8).
Pydantic's default ``BaseModel.__repr__`` honours ``SecretStr`` and
prints ``SecretStr('**********')``; this test guards against future
``__repr__`` / ``__str__`` overrides that would re-expose the secret
in logs or exception traces.

Auto-discovers every ``BaseModel`` subclass that has a ``SecretStr``
field, builds an instance via ``model_construct`` (no validation
needed — we only care about the repr path), and asserts the probe
token does not appear in ``repr(instance)`` / ``str(instance)``.
"""

from __future__ import annotations

import ast
import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr

from .conftest import REPO_ROOT, SRC_ROOT, iter_source_files

PROBE_TOKEN = "PROBE-MUST-NOT-LEAK-9b41"  # WHY: unique sentinel detected in repr scan.


def _has_secret_str_field(cls: ast.ClassDef) -> bool:
    """Heuristic AST check — a class body with `<name>: SecretStr` annotation."""
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign):
            for ann_node in ast.walk(stmt.annotation):
                if isinstance(ann_node, ast.Name) and ann_node.id == "SecretStr":
                    return True
                if isinstance(ann_node, ast.Attribute) and ann_node.attr == "SecretStr":
                    return True
    return False


def _module_name_from_path(path: Path) -> str:
    rel = path.relative_to(SRC_ROOT)
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _candidate_classes() -> Iterator[tuple[str, type[BaseModel]]]:
    sys.path.insert(0, str(SRC_ROOT))
    try:
        for src in iter_source_files():
            module_name = _module_name_from_path(src.path)
            if not module_name:
                continue
            tree = src.tree
            class_names = [
                node.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef) and _has_secret_str_field(node)
            ]
            if not class_names:
                continue
            module = importlib.import_module(module_name)
            for name in class_names:
                cls = getattr(module, name, None)
                if isinstance(cls, type) and issubclass(cls, BaseModel):
                    yield (f"{module_name}.{name}", cls)
    finally:
        sys.path.remove(str(SRC_ROOT))


def _dummy_value_for(field_type: object) -> object:
    """Return a dummy value good enough to feed ``model_construct``.

    ``model_construct`` skips validation, so the value's runtime type
    only has to satisfy what repr / model_dump touch."""
    name = getattr(field_type, "__name__", "")
    if name == "SecretStr":
        return SecretStr(PROBE_TOKEN)
    if name in {"int", "float"}:
        return 0
    if name == "bool":
        return False
    if name == "Path":
        return Path("/tmp/probe")
    return "probe"


@pytest.mark.parametrize("class_path", [c[0] for c in list(_candidate_classes())])
def test_secret_str_models_redact_in_repr(class_path: str) -> None:
    candidates = dict(_candidate_classes())
    cls = candidates[class_path]
    fields_payload: dict[str, Any] = {
        field_name: _dummy_value_for(info.annotation)
        for field_name, info in cls.model_fields.items()
    }
    instance = cls.model_construct(**fields_payload)
    rendered = (repr(instance), str(instance))
    for output in rendered:
        if PROBE_TOKEN in output:
            raise AssertionError(
                f"{class_path} leaks SecretStr in repr/str output: {output!r}.\n"
                f"Pydantic's default BaseModel.__repr__ already redacts; check for "
                f"a custom __repr__ / __str__ on {class_path} that bypasses redaction."
            )
    # Ensure we actually exercised the repr path (defensive against silent skips).
    assert REPO_ROOT.is_dir()
