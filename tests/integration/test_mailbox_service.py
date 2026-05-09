from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import MetaData, select
from sqlalchemy.engine import Engine

from postino_core.enums import (
    DomainTransport,
    MailboxStatus,
    PasswordScheme,
)
from postino_core.errors import (
    AlreadyExistsError,
    CapacityError,
    FilesystemError,
    NotFoundError,
)
from postino_core.fs import FilesystemAdapter
from postino_core.hooks import HookRunner
from postino_core.models import Domain, MailboxCreate
from postino_core.providers.local import LocalProvider
from postino_core.services.mailbox import MailboxService

pytestmark = pytest.mark.integration


def _build_service(
    db: Engine,
    fs: FilesystemAdapter,
    hook: HookRunner,
    clock: Callable[[], datetime],
) -> MailboxService:
    md = MetaData()
    md.reflect(bind=db)
    return MailboxService(
        engine=db,
        identity=LocalProvider(metadata=md),
        fs=fs,
        hooks=hook,
        clock=clock,
        metadata=md,
    )


def _seed_domain(db: Engine, domain: str, max_mailboxes: int) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        conn.execute(md.tables["domain"].insert().values(
            domain=domain,
            description="",
            aliases=0,
            mailboxes=max_mailboxes,
            maxquota=0,
            quota=0,
            transport="virtual",
            backupmx=0,
            active=1,
        ))


def test_mailbox_add_happy_path(
    db: Engine,
    tmp_mail_root: Path,
    fake_postcreation_hook: Path,
    frozen_clock: datetime,
) -> None:
    _seed_domain(db, "example.com", max_mailboxes=10)
    fs = FilesystemAdapter(mail_root=tmp_mail_root, vmail_uid=-1, vmail_gid=-1)
    hook = HookRunner(script_path=fake_postcreation_hook)
    svc = _build_service(db, fs, hook, lambda: frozen_clock)

    created = svc.add(MailboxCreate(
        username="foo@example.com",
        password=SecretStr("hunter2"),
        name="Foo",
        quota_bytes=5 * 1024**3,
        scheme=PasswordScheme.BCRYPT,
    ))
    assert created.username == "foo@example.com"
    assert created.status is MailboxStatus.ACTIVE
    assert (tmp_mail_root / "example.com" / "foo").is_dir()


def test_mailbox_add_duplicate_raises(
    db: Engine,
    tmp_mail_root: Path,
    fake_postcreation_hook: Path,
    frozen_clock: datetime,
) -> None:
    _seed_domain(db, "example.com", max_mailboxes=10)
    svc = _build_service(
        db,
        FilesystemAdapter(mail_root=tmp_mail_root, vmail_uid=-1, vmail_gid=-1),
        HookRunner(script_path=fake_postcreation_hook),
        lambda: frozen_clock,
    )
    create = MailboxCreate(
        username="foo@example.com",
        password=SecretStr("h"),
        name="",
        quota_bytes=0,
        scheme=PasswordScheme.BCRYPT,
    )
    svc.add(create)
    with pytest.raises(AlreadyExistsError):
        svc.add(create)


def test_mailbox_add_unknown_domain_raises(
    db: Engine,
    tmp_mail_root: Path,
    fake_postcreation_hook: Path,
    frozen_clock: datetime,
) -> None:
    svc = _build_service(
        db,
        FilesystemAdapter(mail_root=tmp_mail_root, vmail_uid=-1, vmail_gid=-1),
        HookRunner(script_path=fake_postcreation_hook),
        lambda: frozen_clock,
    )
    with pytest.raises(NotFoundError):
        svc.add(MailboxCreate(
            username="foo@noexist.test",
            password=SecretStr("h"),
            name="",
            quota_bytes=0,
            scheme=PasswordScheme.BCRYPT,
        ))


def test_mailbox_add_capacity_exceeded(
    db: Engine,
    tmp_mail_root: Path,
    fake_postcreation_hook: Path,
    frozen_clock: datetime,
) -> None:
    _seed_domain(db, "tiny.test", max_mailboxes=1)
    svc = _build_service(
        db,
        FilesystemAdapter(mail_root=tmp_mail_root, vmail_uid=-1, vmail_gid=-1),
        HookRunner(script_path=fake_postcreation_hook),
        lambda: frozen_clock,
    )
    svc.add(MailboxCreate(
        username="a@tiny.test", password=SecretStr("p"), name="",
        quota_bytes=0, scheme=PasswordScheme.BCRYPT,
    ))
    with pytest.raises(CapacityError):
        svc.add(MailboxCreate(
            username="b@tiny.test", password=SecretStr("p"), name="",
            quota_bytes=0, scheme=PasswordScheme.BCRYPT,
        ))


def test_mailbox_add_fs_failure_rolls_back_db(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
    frozen_clock: datetime,
) -> None:
    _seed_domain(db, "example.com", max_mailboxes=10)
    # mail_root is a *file*, not a dir — mkdir will fail.
    bad_root = tmp_path / "not-a-dir"
    bad_root.write_text("blocker")
    fs = FilesystemAdapter(mail_root=bad_root, vmail_uid=-1, vmail_gid=-1)
    svc = _build_service(
        db, fs, HookRunner(script_path=fake_postcreation_hook), lambda: frozen_clock
    )
    with pytest.raises(FilesystemError):
        svc.add(MailboxCreate(
            username="foo@example.com",
            password=SecretStr("p"),
            name="",
            quota_bytes=0,
            scheme=PasswordScheme.BCRYPT,
        ))
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        n = conn.execute(
            select(md.tables["mailbox"]).where(
                md.tables["mailbox"].c.username == "foo@example.com"
            )
        ).fetchone()
    assert n is None  # rolled back
