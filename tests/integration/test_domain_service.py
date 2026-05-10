from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import MetaData, select
from sqlalchemy.engine import Engine

from postino_core.enums import DomainTransport
from postino_core.errors import AlreadyExistsError, CapacityError, NotFoundError
from postino_core.fs import FilesystemAdapter
from postino_core.services.domain import DomainService

pytestmark = pytest.mark.integration


def _service(
    db: Engine, frozen_clock: datetime, fs: FilesystemAdapter | None = None
) -> DomainService:
    md = MetaData()
    md.reflect(bind=db)
    if fs is None:
        # Sentinel adapter pointing at a tmp dir that doesn't exist; OK when
        # the test never exercises the FS path.
        fs = FilesystemAdapter(mail_root=Path("/tmp/postino-noop"), vmail_uid=-1, vmail_gid=-1)
    return DomainService(
        engine=db,
        metadata=md,
        clock=lambda: frozen_clock,
        fs=fs,
        lmtp_destination="unix:private/dovecot-lmtp",
    )


def test_domain_add_get(db: Engine, frozen_clock: datetime) -> None:
    svc = _service(db, frozen_clock)
    svc.add(
        domain="example.com",
        description="example",
        max_aliases=0,
        max_mailboxes=10,
        max_quota_bytes=0,
        default_quota_bytes=1024**3,
        transport=DomainTransport.LMTP,
        backupmx=False,
    )
    d = svc.get("example.com")
    assert d is not None
    assert d.transport is DomainTransport.LMTP
    assert d.max_mailboxes == 10


def test_domain_add_lmtp_writes_full_transport_string(db: Engine, frozen_clock: datetime) -> None:
    """The DB cell must hold postfix's full ``lmtp:<nexthop>`` value so
    the postfix transport_maps lookup resolves; the enum carries only
    the protocol (``lmtp``), nexthop comes from PostinoSettings."""
    md = MetaData()
    md.reflect(bind=db)
    svc = DomainService(
        engine=db,
        metadata=md,
        clock=lambda: frozen_clock,
        fs=FilesystemAdapter(mail_root=Path("/tmp/postino-noop"), vmail_uid=-1, vmail_gid=-1),
        lmtp_destination="inet:127.0.0.1:24",
    )
    svc.add(
        domain="lmtp.example.com",
        description="",
        max_aliases=0,
        max_mailboxes=0,
        max_quota_bytes=0,
        default_quota_bytes=0,
        transport=DomainTransport.LMTP,
        backupmx=False,
    )
    with db.begin() as conn:
        raw = conn.execute(
            select(md.tables["domain"].c.transport).where(
                md.tables["domain"].c.domain == "lmtp.example.com"
            )
        ).scalar_one()
    assert raw == "lmtp:inet:127.0.0.1:24"
    # And the round-trip parses back to the protocol enum.
    d = svc.get("lmtp.example.com")
    assert d is not None
    assert d.transport is DomainTransport.LMTP


def test_domain_add_duplicate(db: Engine, frozen_clock: datetime) -> None:
    svc = _service(db, frozen_clock)
    svc.add(
        domain="x.example.org",
        description="",
        max_aliases=0,
        max_mailboxes=0,
        max_quota_bytes=0,
        default_quota_bytes=0,
        transport=DomainTransport.VIRTUAL,
        backupmx=False,
    )
    with pytest.raises(AlreadyExistsError):
        svc.add(
            domain="x.example.org",
            description="",
            max_aliases=0,
            max_mailboxes=0,
            max_quota_bytes=0,
            default_quota_bytes=0,
            transport=DomainTransport.VIRTUAL,
            backupmx=False,
        )


def test_domain_delete(db: Engine, frozen_clock: datetime) -> None:
    svc = _service(db, frozen_clock)
    svc.add(
        domain="x.example.org",
        description="",
        max_aliases=0,
        max_mailboxes=0,
        max_quota_bytes=0,
        default_quota_bytes=0,
        transport=DomainTransport.VIRTUAL,
        backupmx=False,
    )
    svc.delete("x.example.org")
    assert svc.get("x.example.org") is None


def test_domain_delete_missing(db: Engine, frozen_clock: datetime) -> None:
    svc = _service(db, frozen_clock)
    with pytest.raises(NotFoundError):
        svc.delete("ghost.example.org")


def _seed_mailbox(db: Engine, username: str, domain: str) -> None:
    md = MetaData()
    md.reflect(bind=db)
    local_part, _, _ = username.partition("@")
    with db.begin() as conn:
        conn.execute(
            md.tables["mailbox"]
            .insert()
            .values(
                username=username,
                password="{NOAUTH}",
                name="",
                maildir=f"{domain}/{local_part}/",
                quota=0,
                local_part=local_part,
                domain=domain,
                active=1,
                created="2026-05-09 12:00:00",
                modified="2026-05-09 12:00:00",
            )
        )
        conn.execute(md.tables["quota2"].insert().values(username=username, bytes=0, messages=0))


def _seed_alias(db: Engine, address: str, goto: str, domain: str) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        conn.execute(
            md.tables["alias"]
            .insert()
            .values(
                address=address,
                goto=goto,
                domain=domain,
                created="2026-05-09 12:00:00",
                modified="2026-05-09 12:00:00",
                active=1,
            )
        )


def _seed_alias_domain(db: Engine, alias_dom: str, target_dom: str) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        conn.execute(
            md.tables["alias_domain"]
            .insert()
            .values(
                alias_domain=alias_dom,
                target_domain=target_dom,
                created="2026-05-09 12:00:00",
                modified="2026-05-09 12:00:00",
                active=1,
            )
        )


def _seed_domain_admin(db: Engine, admin: str, domain: str) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        conn.execute(
            md.tables["domain_admins"]
            .insert()
            .values(
                username=admin,
                domain=domain,
                created="2026-05-09 12:00:00",
                active=1,
            )
        )


def test_domain_delete_with_mailbox_blocks_without_force(
    db: Engine, frozen_clock: datetime
) -> None:
    svc = _service(db, frozen_clock)
    svc.add(
        domain="busy.example.org",
        description="",
        max_aliases=0,
        max_mailboxes=10,
        max_quota_bytes=0,
        default_quota_bytes=0,
        transport=DomainTransport.VIRTUAL,
        backupmx=False,
    )
    _seed_mailbox(db, "u@busy.example.org", "busy.example.org")
    with pytest.raises(CapacityError):
        svc.delete("busy.example.org")
    # Domain row and mailbox row both still present.
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        d = conn.execute(
            select(md.tables["domain"]).where(md.tables["domain"].c.domain == "busy.example.org")
        ).fetchone()
        m = conn.execute(
            select(md.tables["mailbox"]).where(
                md.tables["mailbox"].c.username == "u@busy.example.org"
            )
        ).fetchone()
    assert d is not None
    assert m is not None


def test_domain_delete_with_alias_blocks_without_force(db: Engine, frozen_clock: datetime) -> None:
    svc = _service(db, frozen_clock)
    svc.add(
        domain="busy.example.org",
        description="",
        max_aliases=0,
        max_mailboxes=0,
        max_quota_bytes=0,
        default_quota_bytes=0,
        transport=DomainTransport.VIRTUAL,
        backupmx=False,
    )
    _seed_alias(db, "x@busy.example.org", "y@elsewhere.example.org", "busy.example.org")
    with pytest.raises(CapacityError):
        svc.delete("busy.example.org")


def test_domain_delete_force_cascades_everything(
    db: Engine, frozen_clock: datetime, tmp_path: Path
) -> None:
    fs = FilesystemAdapter(mail_root=tmp_path, vmail_uid=-1, vmail_gid=-1)
    svc = _service(db, frozen_clock, fs=fs)
    svc.add(
        domain="busy.example.org",
        description="",
        max_aliases=0,
        max_mailboxes=10,
        max_quota_bytes=0,
        default_quota_bytes=0,
        transport=DomainTransport.VIRTUAL,
        backupmx=False,
    )
    _seed_mailbox(db, "u@busy.example.org", "busy.example.org")
    _seed_alias(db, "x@busy.example.org", "y@elsewhere.example.org", "busy.example.org")
    _seed_alias_domain(db, "alt.example.org", "busy.example.org")
    _seed_domain_admin(db, "admin@nope.example.org", "busy.example.org")
    fs.create_maildir(Path("busy.example.org/u/"))
    assert (tmp_path / "busy.example.org" / "u").is_dir()

    svc.delete("busy.example.org", force=True)

    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        for tbl, col, val in (
            ("domain", "domain", "busy.example.org"),
            ("mailbox", "username", "u@busy.example.org"),
            ("quota2", "username", "u@busy.example.org"),
            ("alias", "address", "x@busy.example.org"),
            ("alias_domain", "target_domain", "busy.example.org"),
            ("domain_admins", "domain", "busy.example.org"),
        ):
            t = md.tables[tbl]
            row = conn.execute(select(t).where(t.c[col] == val)).fetchone()
            assert row is None, f"{tbl}.{col}={val!r} still present"
    # Per-domain maildir tree gone.
    assert not (tmp_path / "busy.example.org").exists()


def test_domain_delete_force_on_empty_domain_succeeds(
    db: Engine, frozen_clock: datetime, tmp_path: Path
) -> None:
    fs = FilesystemAdapter(mail_root=tmp_path, vmail_uid=-1, vmail_gid=-1)
    svc = _service(db, frozen_clock, fs=fs)
    svc.add(
        domain="empty.example.org",
        description="",
        max_aliases=0,
        max_mailboxes=0,
        max_quota_bytes=0,
        default_quota_bytes=0,
        transport=DomainTransport.VIRTUAL,
        backupmx=False,
    )
    svc.delete("empty.example.org", force=True)
    assert svc.get("empty.example.org") is None


def test_domain_delete_alias_domain_pointing_at_domain_blocks(
    db: Engine, frozen_clock: datetime
) -> None:
    svc = _service(db, frozen_clock)
    svc.add(
        domain="busy.example.org",
        description="",
        max_aliases=0,
        max_mailboxes=0,
        max_quota_bytes=0,
        default_quota_bytes=0,
        transport=DomainTransport.VIRTUAL,
        backupmx=False,
    )
    _seed_alias_domain(db, "alt.example.org", "busy.example.org")
    with pytest.raises(CapacityError):
        svc.delete("busy.example.org")


def test_domain_list(db: Engine, frozen_clock: datetime) -> None:
    svc = _service(db, frozen_clock)
    for d in ("a.example.org", "b.example.org"):
        svc.add(
            domain=d,
            description="",
            max_aliases=0,
            max_mailboxes=0,
            max_quota_bytes=0,
            default_quota_bytes=0,
            transport=DomainTransport.VIRTUAL,
            backupmx=False,
        )
    out = {d.domain for d in svc.list()}
    assert out == {"a.example.org", "b.example.org"}
