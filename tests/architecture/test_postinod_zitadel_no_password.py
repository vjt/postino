"""Zitadel event handlers never write credentials.

Zitadel owns its users' credentials; postinod's `/zitadel/events`
surface is for lifecycle + profile data only. This AST guard catches a
contributor accidentally piping a Zitadel password payload (none exist
today) into ``MailboxCreate``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, iter_source_files

ZITADEL_ROOT = SRC_ROOT / "postinod" / "zitadel"

_FORBIDDEN = {"password", "scheme"}


def _zitadel_files() -> list[Path]:
    return [src.path for src in iter_source_files() if ZITADEL_ROOT in src.path.parents]


@pytest.mark.parametrize("source_path", _zitadel_files(), ids=lambda p: p.name)
def test_zitadel_never_constructs_mailboxcreate_with_credentials(source_path: Path) -> None:
    tree = ast.parse(source_path.read_text())
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Name) and func.id == "MailboxCreate") or (
            isinstance(func, ast.Attribute) and func.attr == "MailboxCreate"
        ):
            for kw in node.keywords:
                if kw.arg in _FORBIDDEN:
                    offenders.append((node.lineno, kw.arg or ""))
    assert not offenders, (
        f"{source_path}: MailboxCreate(...) under postinod/zitadel/ passes "
        f"forbidden kwarg(s): {offenders}. Zitadel events are credential-free by "
        "contract — IdP owns the credential."
    )


def test_set_password_not_called_in_zitadel() -> None:
    """No call to `mailbox_service.set_password` or `.release_identity` from Zitadel."""
    forbidden_attrs = {"set_password", "release_identity"}
    for src_path in _zitadel_files():
        tree = ast.parse(src_path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_attrs, (
                    f"{src_path}:{node.lineno} calls {node.func.attr!r} — "
                    "Zitadel router must not touch credential lifecycle"
                )
