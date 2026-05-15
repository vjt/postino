"""Unit tests for _check_postcreation_hook_syntax.

Builds tmp_path hook scripts with varied shebangs and contents and
asserts the dispatched finding severity matches the spec.
"""

from __future__ import annotations

from pathlib import Path

from postino_core.check.consistency import (
    _check_postcreation_hook_syntax,  # pyright: ignore[reportPrivateUsage]  # WHY: regression guard for the hook-syntax check; module-private by design.
)
from postino_core.config import PostinoSettings
from postino_core.enums import IdentityBackend, PasswordScheme


def _settings(hook: Path, tmp_path: Path) -> PostinoSettings:
    return PostinoSettings(
        identity_backend=IdentityBackend.LOCAL,
        postfix_sql_dir=tmp_path / "postfix",
        virtual_mailbox_base=tmp_path / "mail",
        postcreation_hook=hook,
        vmail_uid=5000,
        vmail_gid=5000,
        default_password_scheme=PasswordScheme.BCRYPT,
        default_quota_bytes=1024**3,
    )


def _write_hook(p: Path, content: str) -> Path:
    p.write_text(content)
    p.chmod(0o755)
    return p


def test_hook_syntax_valid_sh_passes(tmp_path: Path) -> None:
    hook = _write_hook(tmp_path / "hook.sh", "#!/bin/sh\nset -eu\necho ok\n")
    finding = _check_postcreation_hook_syntax(_settings(hook, tmp_path))
    assert finding.severity == "info"
    assert finding.name == "postcreation_hook_syntax"
    assert "passed" in finding.message
