"""Integration tests for AliasDomainService against MariaDB."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

from postino_core.enums import MailboxStatus
from postino_core.errors import AlreadyExistsError, NotFoundError, RuleViolationError
from postino_core.services.alias_domain import AliasDomainService

pytestmark = pytest.mark.integration


def _seed_domain(db: Engine, domain: str) -> None:
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
                mailboxes=0,
                maxquota=0,
                quota=0,
                transport="virtual",
                backupmx=0,
                active=1,
            )
        )


def _seed_alias_domain(db: Engine, src: str, tgt: str, *, active: int = 1) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        conn.execute(
            md.tables["alias_domain"]
            .insert()
            .values(
                alias_domain=src,
                target_domain=tgt,
                active=active,
            )
        )


def _service(db: Engine, frozen_clock: datetime) -> AliasDomainService:
    md = MetaData()
    md.reflect(bind=db)
    return AliasDomainService(engine=db, metadata=md, clock=lambda: frozen_clock)


def test_list_returns_active_rows(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "src.it")
    _seed_domain(db, "tgt.it")
    _seed_alias_domain(db, "src.it", "tgt.it")
    rows = _service(db, frozen_clock).list()
    assert len(rows) == 1
    assert rows[0].alias_domain == "src.it"
    assert rows[0].target_domain == "tgt.it"
    assert rows[0].status is MailboxStatus.ACTIVE


def test_list_filters_disabled_by_default(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "src.it")
    _seed_domain(db, "tgt.it")
    _seed_alias_domain(db, "src.it", "tgt.it", active=0)
    assert _service(db, frozen_clock).list() == []
    rows = _service(db, frozen_clock).list(include_disabled=True)
    assert len(rows) == 1
    assert rows[0].status is MailboxStatus.DISABLED


def test_list_filter_by_target(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "a.it")
    _seed_domain(db, "b.it")
    _seed_domain(db, "tgt.it")
    _seed_domain(db, "other.it")
    _seed_alias_domain(db, "a.it", "tgt.it")
    _seed_alias_domain(db, "b.it", "other.it")
    rows = _service(db, frozen_clock).list(target="tgt.it")
    assert {r.alias_domain for r in rows} == {"a.it"}


def test_get_returns_row(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "src.it")
    _seed_domain(db, "tgt.it")
    _seed_alias_domain(db, "src.it", "tgt.it")
    got = _service(db, frozen_clock).get("src.it")
    assert got.alias_domain == "src.it"


def test_get_raises_when_missing(db: Engine, frozen_clock: datetime) -> None:
    with pytest.raises(NotFoundError, match=r"alias_domain absent\.it does not exist"):
        _service(db, frozen_clock).get("absent.it")


def test_add_happy_path(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "src.it")
    _seed_domain(db, "tgt.it")
    svc = _service(db, frozen_clock)
    row = svc.add("src.it", target="tgt.it")
    assert row.alias_domain == "src.it"
    assert row.target_domain == "tgt.it"
    assert row.status is MailboxStatus.ACTIVE
    # round-trip via get
    assert svc.get("src.it").target_domain == "tgt.it"


def test_add_rejects_self_alias(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "loop.it")
    with pytest.raises(RuleViolationError, match=r"self-alias"):
        _service(db, frozen_clock).add("loop.it", target="loop.it")


def test_add_rejects_missing_source_domain(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "tgt.it")
    with pytest.raises(NotFoundError, match=r"domain ghost\.it does not exist"):
        _service(db, frozen_clock).add("ghost.it", target="tgt.it")


def test_add_rejects_missing_target_domain(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "src.it")
    with pytest.raises(NotFoundError, match=r"domain ghost\.it does not exist"):
        _service(db, frozen_clock).add("src.it", target="ghost.it")


def test_add_rejects_source_already_target(db: Engine, frozen_clock: datetime) -> None:
    """Rule 4: alias_domain cannot be a target_domain of another row."""
    _seed_domain(db, "a.it")
    _seed_domain(db, "b.it")
    _seed_domain(db, "c.it")
    _seed_alias_domain(db, "c.it", "a.it")  # a.it is now a target
    with pytest.raises(RuleViolationError, match=r"would chain"):
        _service(db, frozen_clock).add("a.it", target="b.it")


def test_add_rejects_target_already_source(db: Engine, frozen_clock: datetime) -> None:
    """Rule 5: target cannot itself be an alias_domain of another row."""
    _seed_domain(db, "a.it")
    _seed_domain(db, "b.it")
    _seed_domain(db, "c.it")
    _seed_alias_domain(db, "b.it", "c.it")  # b.it is now a source
    with pytest.raises(RuleViolationError, match=r"would chain"):
        _service(db, frozen_clock).add("a.it", target="b.it")


def test_add_rejects_duplicate_row(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "src.it")
    _seed_domain(db, "tgt.it")
    _seed_alias_domain(db, "src.it", "tgt.it")
    with pytest.raises(AlreadyExistsError, match=r"alias_domain src\.it already exists"):
        _service(db, frozen_clock).add("src.it", target="tgt.it")


def test_delete_happy(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "src.it")
    _seed_domain(db, "tgt.it")
    _seed_alias_domain(db, "src.it", "tgt.it")
    svc = _service(db, frozen_clock)
    svc.delete("src.it")
    with pytest.raises(NotFoundError):
        svc.get("src.it")


def test_delete_missing_raises(db: Engine, frozen_clock: datetime) -> None:
    with pytest.raises(NotFoundError, match=r"alias_domain ghost\.it does not exist"):
        _service(db, frozen_clock).delete("ghost.it")


def test_set_status_disable_then_enable(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "src.it")
    _seed_domain(db, "tgt.it")
    _seed_alias_domain(db, "src.it", "tgt.it")
    svc = _service(db, frozen_clock)
    svc.set_status("src.it", MailboxStatus.DISABLED)
    assert svc.list() == []  # default list filters disabled out
    assert svc.list(include_disabled=True)[0].status is MailboxStatus.DISABLED
    svc.set_status("src.it", MailboxStatus.ACTIVE)
    assert svc.list()[0].status is MailboxStatus.ACTIVE


def test_set_status_missing_raises(db: Engine, frozen_clock: datetime) -> None:
    with pytest.raises(NotFoundError, match=r"alias_domain ghost\.it does not exist"):
        _service(db, frozen_clock).set_status("ghost.it", MailboxStatus.DISABLED)


def test_retarget_happy(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "src.it")
    _seed_domain(db, "tgt1.it")
    _seed_domain(db, "tgt2.it")
    _seed_alias_domain(db, "src.it", "tgt1.it")
    svc = _service(db, frozen_clock)
    row = svc.retarget("src.it", target="tgt2.it")
    assert row.target_domain == "tgt2.it"
    assert svc.get("src.it").target_domain == "tgt2.it"


def test_retarget_missing_row_raises(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "tgt.it")
    with pytest.raises(NotFoundError, match=r"alias_domain ghost\.it does not exist"):
        _service(db, frozen_clock).retarget("ghost.it", target="tgt.it")


def test_retarget_to_self_raises(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "src.it")
    _seed_domain(db, "tgt.it")
    _seed_alias_domain(db, "src.it", "tgt.it")
    with pytest.raises(RuleViolationError, match=r"self-alias"):
        _service(db, frozen_clock).retarget("src.it", target="src.it")


def test_retarget_to_missing_domain_raises(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "src.it")
    _seed_domain(db, "tgt.it")
    _seed_alias_domain(db, "src.it", "tgt.it")
    with pytest.raises(NotFoundError, match=r"domain ghost\.it does not exist"):
        _service(db, frozen_clock).retarget("src.it", target="ghost.it")


def test_retarget_to_a_source_raises(db: Engine, frozen_clock: datetime) -> None:
    """Rule 5 on retarget: target cannot itself be a source of another alias_domain row."""
    _seed_domain(db, "a.it")
    _seed_domain(db, "b.it")
    _seed_domain(db, "c.it")
    _seed_domain(db, "d.it")
    _seed_alias_domain(db, "a.it", "b.it")
    _seed_alias_domain(db, "c.it", "d.it")
    # c.it is a source → cannot retarget a.it to c.it
    with pytest.raises(RuleViolationError, match=r"would chain"):
        _service(db, frozen_clock).retarget("a.it", target="c.it")
