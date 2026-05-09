import pytest
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

from postino_core.services.quota import QuotaService

pytestmark = pytest.mark.integration


def _service(db: Engine) -> QuotaService:
    md = MetaData()
    md.reflect(bind=db)
    return QuotaService(engine=db, metadata=md)


def _seed_quota(db: Engine, username: str, used: int, msgs: int) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        conn.execute(md.tables["quota2"].insert().values(
            username=username, bytes=used, messages=msgs,
        ))


def test_quota_show_one(db: Engine) -> None:
    _seed_quota(db, "foo@example.com", 1024, 3)
    u = _service(db).show("foo@example.com")
    assert u is not None and u.bytes_used == 1024 and u.messages == 3


def test_quota_show_missing_returns_none(db: Engine) -> None:
    assert _service(db).show("ghost@example.com") is None


def test_quota_show_all(db: Engine) -> None:
    _seed_quota(db, "a@x.test", 100, 1)
    _seed_quota(db, "b@x.test", 200, 2)
    out = {u.username: u.bytes_used for u in _service(db).list()}
    assert out == {"a@x.test": 100, "b@x.test": 200}
