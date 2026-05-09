from datetime import datetime

import pytest
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

from postino_core.errors import AlreadyExistsError, NotFoundError
from postino_core.services.alias import AliasService

pytestmark = pytest.mark.integration


def _seed_domain(db: Engine, domain: str) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        conn.execute(md.tables["domain"].insert().values(
            domain=domain, description="", aliases=0, mailboxes=0,
            maxquota=0, quota=0, transport="virtual", backupmx=0, active=1,
        ))


def _service(db: Engine, frozen_clock: datetime) -> AliasService:
    md = MetaData()
    md.reflect(bind=db)
    return AliasService(engine=db, metadata=md, clock=lambda: frozen_clock)


def test_alias_add_get(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "example.com")
    svc = _service(db, frozen_clock)
    svc.add(address="foo@example.com", goto="bar@example.com")
    a = svc.get("foo@example.com")
    assert a is not None
    assert a.goto == "bar@example.com"


def test_alias_add_duplicate_raises(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "example.com")
    svc = _service(db, frozen_clock)
    svc.add(address="foo@example.com", goto="a@x.test")
    with pytest.raises(AlreadyExistsError):
        svc.add(address="foo@example.com", goto="b@x.test")


def test_alias_delete(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "example.com")
    svc = _service(db, frozen_clock)
    svc.add(address="foo@example.com", goto="bar@example.com")
    svc.delete("foo@example.com")
    assert svc.get("foo@example.com") is None


def test_alias_delete_missing_raises(db: Engine, frozen_clock: datetime) -> None:
    svc = _service(db, frozen_clock)
    with pytest.raises(NotFoundError):
        svc.delete("ghost@example.com")


def test_alias_list_by_domain(db: Engine, frozen_clock: datetime) -> None:
    _seed_domain(db, "example.com")
    _seed_domain(db, "other.test")
    svc = _service(db, frozen_clock)
    svc.add(address="a@example.com", goto="x@x.test")
    svc.add(address="b@example.com", goto="x@x.test")
    svc.add(address="c@other.test", goto="x@x.test")
    addresses = {a.address for a in svc.list(domain="example.com")}
    assert addresses == {"a@example.com", "b@example.com"}
