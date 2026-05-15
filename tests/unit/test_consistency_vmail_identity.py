"""Unit tests for _check_vmail_identity.

Monkeypatches pwd/grp so tests don't depend on which users exist on
the host that runs them.
"""

from __future__ import annotations

import pwd
import grp
from pathlib import Path
from types import SimpleNamespace

import pytest

from postino_core.check.consistency import Finding, _check_vmail_identity
from postino_core.config import PostinoSettings
from postino_core.enums import IdentityBackend, PasswordScheme


def _settings(tmp_path: Path, *, uid: int = 5000, gid: int = 5000) -> PostinoSettings:
    return PostinoSettings(
        identity_backend=IdentityBackend.LOCAL,
        postfix_sql_dir=tmp_path / "postfix",
        virtual_mailbox_base=tmp_path / "mail",
        postcreation_hook=Path("/bin/true"),
        vmail_uid=uid,
        vmail_gid=gid,
        default_password_scheme=PasswordScheme.BCRYPT,
        default_quota_bytes=1024**3,
    )


def _fake_pw(name: str) -> SimpleNamespace:
    return SimpleNamespace(pw_name=name)


def _fake_gr(name: str) -> SimpleNamespace:
    return SimpleNamespace(gr_name=name)


def test_vmail_identity_uid_and_gid_resolve_to_vmail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: _fake_pw("vmail"))
    monkeypatch.setattr(grp, "getgrgid", lambda gid: _fake_gr("vmail"))
    findings = _check_vmail_identity(_settings(tmp_path))
    assert [f.severity for f in findings] == ["info", "info"]
    assert [f.name for f in findings] == ["vmail_uid", "vmail_gid"]
