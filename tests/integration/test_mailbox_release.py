"""MailboxService.release_identity — DB-touching integration test.

Marked `integration` per pyproject.toml: requires POSTINO_TEST_DB_URL.

The existing integration suite builds services inline (no shared
``service_bundle`` fixture — see ``test_mailbox_service.py``), so we
keep the same shape here and parametrize the identity provider per
test instead of adding new conftest fixtures.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import MetaData, select
from sqlalchemy.engine import Engine

from postino_core.enums import MailboxStatus, PasswordScheme
from postino_core.errors import ConfigError, NotFoundError
from postino_core.fs import FilesystemAdapter
from postino_core.hooks import HookRunner
from postino_core.models import MailboxCreate
from postino_core.providers.base import SENTINEL_NOAUTH
from postino_core.providers.hybrid import HybridProvider
from postino_core.providers.local import LocalProvider
from postino_core.services.mailbox import MailboxService

pytestmark = pytest.mark.integration


def _seed_domain(db: Engine, domain: str, max_mailboxes: int) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        conn.execute(
            md.tables["domain"]
            .insert()
            .values(
                domain=domain,
                description="",
                aliases=0,
                mailboxes=max_mailboxes,
                maxquota=0,
                quota=0,
                transport="virtual",
                backupmx=0,
                active=1,
            )
        )


def _build_service(
    db: Engine,
    fs: FilesystemAdapter,
    hook: HookRunner,
    clock: Callable[[], datetime],
    *,
    backend: str,
) -> MailboxService:
    md = MetaData()
    md.reflect(bind=db)
    identity = (
        HybridProvider(metadata=md, clock=clock)
        if backend == "hybrid"
        else LocalProvider(metadata=md, clock=clock)
    )
    return MailboxService(
        engine=db,
        identity=identity,
        fs=fs,
        hooks=hook,
        clock=clock,
        metadata=md,
    )


def _seed_mailbox(svc: MailboxService, username: str) -> None:
    svc.add(
        MailboxCreate(
            username=username,
            password=SecretStr("hunter2"),
            name="",
            quota_bytes=0,
            scheme=PasswordScheme.BCRYPT,
        )
    )


def test_release_identity_writes_sentinel(
    db: Engine,
    tmp_mail_root: Path,
    fake_postcreation_hook: Path,
    frozen_clock: datetime,
) -> None:
    """Hybrid backend: SQL-authed row → release → password reset to {NOAUTH}."""
    _seed_domain(db, "example.com", max_mailboxes=10)
    svc = _build_service(
        db,
        FilesystemAdapter(mail_root=tmp_mail_root, vmail_uid=-1, vmail_gid=-1),
        HookRunner(script_path=fake_postcreation_hook),
        lambda: frozen_clock,
        backend="hybrid",
    )
    _seed_mailbox(svc, "foo@example.com")
    # Sanity: BCRYPT hash is not the sentinel after add+set_password.
    svc.set_password("foo@example.com", SecretStr("hunter2"), PasswordScheme.BCRYPT)

    svc.release_identity("foo@example.com")

    md = MetaData()
    md.reflect(bind=db)
    mb = md.tables["mailbox"]
    with db.connect() as conn:
        row = conn.execute(mb.select().where(mb.c.username == "foo@example.com")).fetchone()
    assert row is not None
    assert str(row._mapping["password"]) == SENTINEL_NOAUTH  # type: ignore[index]  # WHY: SQLAlchemy RowMapping[str, Any] indexing.

    # And an audit row was written under action=mailbox.release.
    log = md.tables["log"]
    with db.connect() as conn:
        actions = [
            str(r._mapping["action"])  # type: ignore[index]  # WHY: SQLAlchemy RowMapping[str, Any] indexing.
            for r in conn.execute(select(log).where(log.c.data == "foo@example.com")).fetchall()
        ]
    assert "postino.mailbox.release" in actions


def test_release_identity_under_local_backend_raises(
    db: Engine,
    tmp_mail_root: Path,
    fake_postcreation_hook: Path,
    frozen_clock: datetime,
) -> None:
    """LocalProvider rejects release with ConfigError."""
    _seed_domain(db, "example.com", max_mailboxes=10)
    svc = _build_service(
        db,
        FilesystemAdapter(mail_root=tmp_mail_root, vmail_uid=-1, vmail_gid=-1),
        HookRunner(script_path=fake_postcreation_hook),
        lambda: frozen_clock,
        backend="local",
    )
    _seed_mailbox(svc, "foo@example.com")
    with pytest.raises(ConfigError, match="local backend does not release"):
        svc.release_identity("foo@example.com")


def test_release_identity_missing_mailbox_raises(
    db: Engine,
    tmp_mail_root: Path,
    fake_postcreation_hook: Path,
    frozen_clock: datetime,
) -> None:
    """Hybrid backend: releasing a non-existent mailbox raises NotFoundError."""
    _seed_domain(db, "example.com", max_mailboxes=10)
    svc = _build_service(
        db,
        FilesystemAdapter(mail_root=tmp_mail_root, vmail_uid=-1, vmail_gid=-1),
        HookRunner(script_path=fake_postcreation_hook),
        lambda: frozen_clock,
        backend="hybrid",
    )
    with pytest.raises(NotFoundError):
        svc.release_identity("ghost@example.com")


def test_release_identity_idempotent_on_sentinel(
    db: Engine,
    tmp_mail_root: Path,
    fake_postcreation_hook: Path,
    frozen_clock: datetime,
) -> None:
    """Hybrid backend: row already on sentinel is a DB-level no-op.

    Per the HybridProvider contract: rows already on {NOAUTH} return
    without password column changes. MailboxService still writes the
    audit row to record operator intent. Verify it doesn't blow up
    and the password column stays on the sentinel (mailbox row
    inserted by add() carries the sentinel until set_password runs)."""
    _seed_domain(db, "example.com", max_mailboxes=10)
    svc = _build_service(
        db,
        FilesystemAdapter(mail_root=tmp_mail_root, vmail_uid=-1, vmail_gid=-1),
        HookRunner(script_path=fake_postcreation_hook),
        lambda: frozen_clock,
        backend="hybrid",
    )
    _seed_mailbox(svc, "foo@example.com")
    svc.release_identity("foo@example.com")  # first call: hash → sentinel
    svc.release_identity("foo@example.com")  # second call: sentinel → sentinel, no-op

    md = MetaData()
    md.reflect(bind=db)
    mb = md.tables["mailbox"]
    with db.connect() as conn:
        row = conn.execute(mb.select().where(mb.c.username == "foo@example.com")).fetchone()
    assert row is not None
    assert str(row._mapping["password"]) == SENTINEL_NOAUTH  # type: ignore[index]  # WHY: SQLAlchemy RowMapping[str, Any] indexing.
    assert row._mapping["active"] == int(MailboxStatus.ACTIVE)  # type: ignore[index]  # WHY: SQLAlchemy RowMapping[str, Any] indexing.
