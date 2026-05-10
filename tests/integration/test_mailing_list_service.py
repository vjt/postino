"""Integration tests for MailingListService — real DB + tmp spool + real mlmmj binaries."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import MetaData, select
from sqlalchemy.engine import Engine

from postino_core.adapters.mlmmj import MlmmjAdapter
from postino_core.enums import DomainTransport
from postino_core.errors import AlreadyExistsError, CapacityError, ConfigError, NotFoundError
from postino_core.fs import FilesystemAdapter
from postino_core.models import MailingListCreate
from postino_core.services.domain import DomainService
from postino_core.services.mailing_list import MailingListService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("mlmmj-make-ml") is None,
        reason="mlmmj-make-ml not on PATH; install mlmmj 1.3.x to run this suite",
    ),
]


def _noop_fs() -> FilesystemAdapter:
    return FilesystemAdapter(mail_root=Path("/tmp/postino-noop"), vmail_uid=-1, vmail_gid=-1)


def _seed_mlmmj_domain(db: Engine, frozen_clock: datetime, fqdn: str) -> None:
    md = MetaData()
    md.reflect(bind=db)
    DomainService(
        engine=db,
        metadata=md,
        clock=lambda: frozen_clock,
        fs=_noop_fs(),
        lmtp_destination="unix:private/dovecot-lmtp",
    ).add(
        domain=fqdn,
        description=f"mlmmj test {fqdn}",
        max_aliases=0,
        max_mailboxes=0,
        max_quota_bytes=0,
        default_quota_bytes=0,
        transport=DomainTransport.MLMMJ,
        backupmx=False,
    )


def _service(db: Engine, frozen_clock: datetime, spool: Path) -> MailingListService:
    md = MetaData()
    md.reflect(bind=db)
    adapter = MlmmjAdapter(spool_root=spool, mlmmj_uid=-1, mlmmj_gid=-1, timeout=10.0)
    return MailingListService(
        engine=db,
        metadata=md,
        adapter=adapter,
        clock=lambda: frozen_clock,
    )


def test_add_creates_list_and_writes_audit(
    db: Engine, frozen_clock: datetime, tmp_path: Path
) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    _seed_mlmmj_domain(db, frozen_clock, "lists.example.org")
    svc = _service(db, frozen_clock, spool)

    ml = svc.add(
        MailingListCreate(
            address="team@lists.example.org",
            owners=["alice@example.org"],
        )
    )
    assert ml.address == "team@lists.example.org"
    assert ml.owners == ["alice@example.org"]
    assert (spool / "team@lists.example.org" / "control" / "owner").exists()

    md = MetaData()
    md.reflect(bind=db)
    log = md.tables["log"]
    with db.begin() as conn:
        row = conn.execute(
            select(log).where(log.c.action == "postino.mailing_list.create")
        ).fetchone()
    assert row is not None


def test_add_rejects_when_domain_transport_not_mlmmj(
    db: Engine, frozen_clock: datetime, tmp_path: Path
) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    md = MetaData()
    md.reflect(bind=db)
    DomainService(
        engine=db,
        metadata=md,
        clock=lambda: frozen_clock,
        fs=_noop_fs(),
        lmtp_destination="unix:private/dovecot-lmtp",
    ).add(
        domain="lists.example.org",
        description="",
        max_aliases=0,
        max_mailboxes=0,
        max_quota_bytes=0,
        default_quota_bytes=0,
        transport=DomainTransport.VIRTUAL,
        backupmx=False,
    )
    svc = _service(db, frozen_clock, spool)
    with pytest.raises(ConfigError):
        svc.add(MailingListCreate(address="team@lists.example.org", owners=["alice@example.org"]))


def test_add_rejects_collision_with_mailbox(
    db: Engine, frozen_clock: datetime, tmp_path: Path
) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    _seed_mlmmj_domain(db, frozen_clock, "lists.example.org")
    md = MetaData()
    md.reflect(bind=db)
    # Seed a colliding mailbox row.
    with db.begin() as conn:
        conn.execute(
            md.tables["mailbox"]
            .insert()
            .values(
                username="team@lists.example.org",
                password="{NOAUTH}",
                name="Team",
                maildir="lists.example.org/team/",
                quota=0,
                local_part="team",
                domain="lists.example.org",
                active=1,
                created=frozen_clock,
                modified=frozen_clock,
            )
        )
    svc = _service(db, frozen_clock, spool)
    with pytest.raises(AlreadyExistsError):
        svc.add(MailingListCreate(address="team@lists.example.org", owners=["alice@example.org"]))


def test_add_multi_owner_writes_all_owners(
    db: Engine, frozen_clock: datetime, tmp_path: Path
) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    _seed_mlmmj_domain(db, frozen_clock, "lists.example.org")
    svc = _service(db, frozen_clock, spool)
    ml = svc.add(
        MailingListCreate(
            address="team@lists.example.org",
            owners=["alice@example.org", "bob@example.org", "carol@example.org"],
        )
    )
    assert sorted(ml.owners) == ["alice@example.org", "bob@example.org", "carol@example.org"]
    contents = (spool / "team@lists.example.org" / "control" / "owner").read_text().splitlines()
    assert sorted(contents) == ["alice@example.org", "bob@example.org", "carol@example.org"]


def test_add_compensating_cleanup_removes_spool_on_owner_append_failure(
    db: Engine, frozen_clock: datetime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If append_owner raises after create succeeds, the half-built spool dir
    must be removed before the exception propagates."""
    spool = tmp_path / "spool"
    spool.mkdir()
    _seed_mlmmj_domain(db, frozen_clock, "lists.example.org")
    svc = _service(db, frozen_clock, spool)

    def boom(self: object, *, address: object, owner: object) -> None:
        raise RuntimeError("synthetic failure for compensation test")

    monkeypatch.setattr(MlmmjAdapter, "append_owner", boom)

    with pytest.raises(RuntimeError):
        svc.add(
            MailingListCreate(
                address="team@lists.example.org",
                owners=["alice@example.org", "bob@example.org"],
            )
        )
    assert not (spool / "team@lists.example.org").exists()


def test_subscribe_unsubscribe_round_trip(
    db: Engine, frozen_clock: datetime, tmp_path: Path
) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    _seed_mlmmj_domain(db, frozen_clock, "lists.example.org")
    svc = _service(db, frozen_clock, spool)
    svc.add(MailingListCreate(address="team@lists.example.org", owners=["alice@example.org"]))
    svc.subscribe(address="team@lists.example.org", email="bob@example.org")
    ml = svc.get("team@lists.example.org")
    assert ml is not None
    assert ml.subscriber_count == 1

    svc.unsubscribe(address="team@lists.example.org", email="bob@example.org")
    ml = svc.get("team@lists.example.org")
    assert ml is not None
    assert ml.subscriber_count == 0


def test_subscribe_writes_audit_row(db: Engine, frozen_clock: datetime, tmp_path: Path) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    _seed_mlmmj_domain(db, frozen_clock, "lists.example.org")
    svc = _service(db, frozen_clock, spool)
    svc.add(MailingListCreate(address="team@lists.example.org", owners=["alice@example.org"]))
    svc.subscribe(address="team@lists.example.org", email="bob@example.org")

    md = MetaData()
    md.reflect(bind=db)
    log = md.tables["log"]
    with db.begin() as conn:
        rows = conn.execute(
            select(log.c.action, log.c.data).where(log.c.action == "postino.mailing_list.subscribe")
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "team@lists.example.org bob@example.org"


def test_delete_refuses_non_empty_without_force(
    db: Engine, frozen_clock: datetime, tmp_path: Path
) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    _seed_mlmmj_domain(db, frozen_clock, "lists.example.org")
    svc = _service(db, frozen_clock, spool)
    svc.add(MailingListCreate(address="team@lists.example.org", owners=["alice@example.org"]))
    svc.subscribe(address="team@lists.example.org", email="bob@example.org")
    with pytest.raises(CapacityError):
        svc.delete("team@lists.example.org")
    assert (spool / "team@lists.example.org").exists()


def test_delete_with_force_removes_non_empty_list(
    db: Engine, frozen_clock: datetime, tmp_path: Path
) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    _seed_mlmmj_domain(db, frozen_clock, "lists.example.org")
    svc = _service(db, frozen_clock, spool)
    svc.add(MailingListCreate(address="team@lists.example.org", owners=["alice@example.org"]))
    svc.subscribe(address="team@lists.example.org", email="bob@example.org")
    svc.delete("team@lists.example.org", force=True)
    assert not (spool / "team@lists.example.org").exists()


def test_delete_raises_not_found_for_unknown_list(
    db: Engine, frozen_clock: datetime, tmp_path: Path
) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    _seed_mlmmj_domain(db, frozen_clock, "lists.example.org")
    svc = _service(db, frozen_clock, spool)
    with pytest.raises(NotFoundError):
        svc.delete("missing@lists.example.org")
