from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import MetaData, select
from sqlalchemy.engine import Engine

from postino_core.enums import DomainTransport
from postino_core.errors import AlreadyExistsError, CapacityError, ConfigError, NotFoundError
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


def test_domain_delete_keep_maildir_preserves_tree(
    db: Engine, frozen_clock: datetime, tmp_path: Path
) -> None:
    """`keep_maildir=True` cascades the DB rows but leaves the maildir tree."""
    fs = FilesystemAdapter(mail_root=tmp_path, vmail_uid=-1, vmail_gid=-1)
    svc = _service(db, frozen_clock, fs=fs)
    svc.add(
        domain="archive.example.org",
        description="",
        max_aliases=0,
        max_mailboxes=10,
        max_quota_bytes=0,
        default_quota_bytes=0,
        transport=DomainTransport.VIRTUAL,
        backupmx=False,
    )
    _seed_mailbox(db, "u@archive.example.org", "archive.example.org")
    fs.create_maildir(Path("archive.example.org/u/"))

    svc.delete("archive.example.org", force=True, keep_maildir=True)

    assert svc.get("archive.example.org") is None
    # The maildir tree survives — the operator plans to archive it.
    assert (tmp_path / "archive.example.org" / "u").is_dir()


def test_domain_delete_force_fs_failure_rolls_back_db(
    db: Engine, frozen_clock: datetime, tmp_path: Path
) -> None:
    """An FS rmtree failure during force-delete must roll back the DB cascade.

    The privacy-axis fix (review A3.8): pre-v0.4 the FS step ran AFTER the
    DB tx committed and any failure was swallowed, leaving the tenant's
    maildir tree on disk where a same-named re-provisioned mailbox could
    adopt it. v0.4 moves rmtree into the same transaction so an FS error
    aborts the DB cascade — operator sees one error and a consistent
    DB+FS state instead of a half-deleted tenant.
    """
    import shutil

    fs = FilesystemAdapter(mail_root=tmp_path, vmail_uid=-1, vmail_gid=-1)

    # Patch fs.remove_maildir to raise after we've validated/cascaded.
    original_remove = fs.remove_maildir

    def boom(path: Path) -> None:
        del path
        raise OSError("simulated rmtree failure")

    fs.remove_maildir = boom  # type: ignore[method-assign]  # WHY: test-only monkeypatch
    try:
        svc = _service(db, frozen_clock, fs=fs)
        svc.add(
            domain="boom.example.org",
            description="",
            max_aliases=0,
            max_mailboxes=10,
            max_quota_bytes=0,
            default_quota_bytes=0,
            transport=DomainTransport.VIRTUAL,
            backupmx=False,
        )
        _seed_mailbox(db, "u@boom.example.org", "boom.example.org")
        (tmp_path / "boom.example.org" / "u").mkdir(parents=True)

        with pytest.raises(OSError, match="simulated rmtree failure"):
            svc.delete("boom.example.org", force=True)

        # DB cascade must have been rolled back — domain row and mailbox
        # row both still present.
        md = MetaData()
        md.reflect(bind=db)
        with db.begin() as conn:
            d = conn.execute(
                select(md.tables["domain"]).where(
                    md.tables["domain"].c.domain == "boom.example.org"
                )
            ).fetchone()
            m = conn.execute(
                select(md.tables["mailbox"]).where(
                    md.tables["mailbox"].c.username == "u@boom.example.org"
                )
            ).fetchone()
        assert d is not None, "domain row vanished despite FS rollback"
        assert m is not None, "mailbox row vanished despite FS rollback"
    finally:
        fs.remove_maildir = original_remove  # type: ignore[method-assign]  # WHY: restore patch
        shutil.rmtree(tmp_path / "boom.example.org", ignore_errors=True)


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


# ---------------------------------------------------------------------------
# PA ALL pseudo-domain regression tests (production crash on postino domain list)
# ---------------------------------------------------------------------------


def _insert_pa_all_row(db: Engine, md: MetaData, frozen_clock: datetime) -> None:
    """Seed PostfixAdmin's 'ALL' pseudo-row directly — bypassing DomainService.add()."""
    domain = md.tables["domain"]
    with db.begin() as conn:
        conn.execute(
            domain.insert().values(
                domain="ALL",
                description="",
                aliases=0,
                mailboxes=0,
                maxquota=0,
                quota=0,
                transport="",  # empty transport is the PA convention
                backupmx=0,
                created=frozen_clock,
                modified=frozen_clock,
                active=1,
            )
        )


def test_domain_list_skips_pa_all_pseudo_row(db: Engine, frozen_clock: datetime) -> None:
    """PostfixAdmin's `domain='ALL'` pseudo-row must not appear in list().

    Regression: DomainTransport('') raised ValueError when list() tried to
    map the empty transport string to the enum, crashing `postino domain list`
    on any PostfixAdmin-managed server with super-admin accounts configured.
    """
    md = MetaData()
    md.reflect(bind=db)
    _insert_pa_all_row(db, md, frozen_clock)

    svc = _service(db, frozen_clock)
    items = svc.list()
    assert all(d.domain != "ALL" for d in items)


def test_domain_get_returns_none_for_pa_all_pseudo_row(db: Engine, frozen_clock: datetime) -> None:
    """get('ALL') must return None — the pseudo-row has no routable semantics."""
    md = MetaData()
    md.reflect(bind=db)
    _insert_pa_all_row(db, md, frozen_clock)

    svc = _service(db, frozen_clock)
    assert svc.get("ALL") is None


def test_domain_delete_rejects_pa_all_pseudo_row(db: Engine, frozen_clock: datetime) -> None:
    """delete('ALL') must raise NotFoundError — admins must not drop PA's permission row."""
    svc = _service(db, frozen_clock)
    with pytest.raises(NotFoundError, match="ALL"):
        svc.delete("ALL")


def test_domain_add_rejects_pa_all_name(db: Engine, frozen_clock: datetime) -> None:
    """Domain literally named 'ALL' is reserved by PostfixAdmin; reject with ConfigError."""
    svc = _service(db, frozen_clock)
    with pytest.raises(ConfigError, match="ALL"):
        svc.add(
            domain="ALL",
            description="",
            max_aliases=0,
            max_mailboxes=0,
            max_quota_bytes=0,
            default_quota_bytes=0,
            transport=DomainTransport.VIRTUAL,
            backupmx=False,
        )


def test_domain_add_mlmmj_round_trip(db: Engine, frozen_clock: datetime) -> None:
    """`transport='mlmmj'` must round-trip through DomainService.add/get."""
    svc = _service(db, frozen_clock)
    svc.add(
        domain="lists.example.org",
        description="",
        max_aliases=0,
        max_mailboxes=0,
        max_quota_bytes=0,
        default_quota_bytes=0,
        transport=DomainTransport.MLMMJ,
        backupmx=False,
    )
    d = svc.get("lists.example.org")
    assert d is not None
    assert d.transport is DomainTransport.MLMMJ
