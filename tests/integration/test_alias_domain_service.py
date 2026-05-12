"""Integration tests for AliasDomainService against MariaDB."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

from postino_core.enums import MailboxStatus
from postino_core.errors import NotFoundError
from postino_core.services.alias_domain import AliasDomainService

pytestmark = pytest.mark.integration


def _seed_domain(db: Engine, domain: str) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        conn.execute(
            md.tables["domain"].insert().values(
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
            md.tables["alias_domain"].insert().values(
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
