from pathlib import Path

import pytest
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

from postino_core.check.consistency import CheckResult, run_consistency_check
from postino_core.config import PostinoSettings
from postino_core.enums import IdentityBackend, PasswordScheme

pytestmark = pytest.mark.integration


def _settings(tmp_path: Path, hook: Path) -> PostinoSettings:
    sql_dir = tmp_path / "postfix"
    sql_dir.mkdir()
    (sql_dir / "sql-virtual_mailbox_maps.cf").write_text(
        "hosts = localhost\nuser = postfix\npassword = sekret\ndbname = postfix\n"
    )
    return PostinoSettings(
        identity_backend=IdentityBackend.LOCAL,
        postfix_sql_dir=sql_dir,
        virtual_mailbox_base=tmp_path / "mail",
        postcreation_hook=hook,
        vmail_uid=1006,
        vmail_gid=1006,
        default_password_scheme=PasswordScheme.BCRYPT,
        default_quota_bytes=1024**3,
    )


def test_check_passes_with_executable_hook(
    db: Engine, tmp_path: Path, fake_postcreation_hook: Path,
) -> None:
    s = _settings(tmp_path, fake_postcreation_hook)
    s.virtual_mailbox_base.mkdir()
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md)
    assert isinstance(result, CheckResult)
    assert result.ok is True


def test_check_fails_when_hook_not_executable(
    db: Engine, tmp_path: Path,
) -> None:
    hook = tmp_path / "hook.sh"
    hook.write_text("#!/bin/sh\nexit 0\n")
    # NOT chmodded
    s = _settings(tmp_path, hook)
    s.virtual_mailbox_base.mkdir()
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md)
    assert result.ok is False
    assert any("hook" in finding.message.lower() for finding in result.findings)


def test_check_fails_when_mailbox_base_missing(
    db: Engine, tmp_path: Path, fake_postcreation_hook: Path,
) -> None:
    s = _settings(tmp_path, fake_postcreation_hook)
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md)
    assert result.ok is False
    assert any("virtual_mailbox_base" in f.message for f in result.findings)
