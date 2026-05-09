from datetime import datetime

import pytest
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

from postino_core.enums import DomainTransport, MailboxStatus
from postino_core.errors import AlreadyExistsError, NotFoundError
from postino_core.services.domain import DomainService

pytestmark = pytest.mark.integration


def _service(db: Engine, frozen_clock: datetime) -> DomainService:
    md = MetaData()
    md.reflect(bind=db)
    return DomainService(engine=db, metadata=md, clock=lambda: frozen_clock)


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


def test_domain_add_duplicate(db: Engine, frozen_clock: datetime) -> None:
    svc = _service(db, frozen_clock)
    svc.add(domain="x.test", description="", max_aliases=0, max_mailboxes=0,
            max_quota_bytes=0, default_quota_bytes=0,
            transport=DomainTransport.VIRTUAL, backupmx=False)
    with pytest.raises(AlreadyExistsError):
        svc.add(domain="x.test", description="", max_aliases=0, max_mailboxes=0,
                max_quota_bytes=0, default_quota_bytes=0,
                transport=DomainTransport.VIRTUAL, backupmx=False)


def test_domain_delete(db: Engine, frozen_clock: datetime) -> None:
    svc = _service(db, frozen_clock)
    svc.add(domain="x.test", description="", max_aliases=0, max_mailboxes=0,
            max_quota_bytes=0, default_quota_bytes=0,
            transport=DomainTransport.VIRTUAL, backupmx=False)
    svc.delete("x.test")
    assert svc.get("x.test") is None


def test_domain_delete_missing(db: Engine, frozen_clock: datetime) -> None:
    svc = _service(db, frozen_clock)
    with pytest.raises(NotFoundError):
        svc.delete("ghost.test")


def test_domain_list(db: Engine, frozen_clock: datetime) -> None:
    svc = _service(db, frozen_clock)
    for d in ("a.test", "b.test"):
        svc.add(domain=d, description="", max_aliases=0, max_mailboxes=0,
                max_quota_bytes=0, default_quota_bytes=0,
                transport=DomainTransport.VIRTUAL, backupmx=False)
    out = {d.domain for d in svc.list()}
    assert out == {"a.test", "b.test"}
