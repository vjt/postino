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


def test_hook_syntax_invalid_sh_errors(tmp_path: Path) -> None:
    # `done` without matching `do` → syntax error
    hook = _write_hook(tmp_path / "hook.sh", "#!/bin/sh\nif true; then\necho ok\ndone\n")
    finding = _check_postcreation_hook_syntax(_settings(hook, tmp_path))
    assert finding.severity == "error"
    assert "failed" in finding.message


def test_hook_syntax_env_bash_shebang_resolves(tmp_path: Path) -> None:
    hook = _write_hook(tmp_path / "hook.sh", "#!/usr/bin/env bash\nset -eu\necho ok\n")
    finding = _check_postcreation_hook_syntax(_settings(hook, tmp_path))
    # bash may or may not be installed on the host; both "passed" and
    # "not on PATH, skipped" are acceptable here. The key assertion is
    # that the shebang was recognized as a shell interpreter (not the
    # python/skip path).
    assert finding.severity == "info"
    assert "passed" in finding.message or "not on PATH" in finding.message


def test_hook_syntax_python_shebang_skipped(tmp_path: Path) -> None:
    hook = _write_hook(tmp_path / "hook.py", "#!/usr/bin/python3\nprint('ok')\n")
    finding = _check_postcreation_hook_syntax(_settings(hook, tmp_path))
    assert finding.severity == "info"
    assert "non-shell" in finding.message
    assert "python3" in finding.message


def test_hook_syntax_no_shebang_skipped(tmp_path: Path) -> None:
    hook = _write_hook(tmp_path / "hook.sh", "echo no shebang here\n")
    finding = _check_postcreation_hook_syntax(_settings(hook, tmp_path))
    assert finding.severity == "info"
    assert "no shebang" in finding.message


def test_hook_syntax_unreadable_defers(tmp_path: Path) -> None:
    hook = tmp_path / "missing.sh"  # don't create
    finding = _check_postcreation_hook_syntax(_settings(hook, tmp_path))
    assert finding.severity == "info"
    assert "read failed" in finding.message
