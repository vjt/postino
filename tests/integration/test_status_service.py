"""StatusService — row-count snapshot of the PA tables postino owns."""

from __future__ import annotations

import pytest
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

from postino_core.services.status import StatusReport, StatusService

pytestmark = pytest.mark.integration


def _seed(db: Engine, *, domains: int, mailboxes: int, aliases: int, quotas: int) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        for i in range(domains):
            conn.execute(
                md.tables["domain"]
                .insert()
                .values(
                    domain=f"d{i}.example.com",
                    description="",
                    aliases=0,
                    mailboxes=10,
                    maxquota=0,
                    quota=0,
                    transport="virtual",
                    backupmx=0,
                    active=1,
                )
            )
        for i in range(mailboxes):
            conn.execute(
                md.tables["mailbox"]
                .insert()
                .values(
                    username=f"u{i}@d0.example.com",
                    password="{NOAUTH}",
                    name="",
                    maildir=f"d0.example.com/u{i}/",
                    quota=0,
                    local_part=f"u{i}",
                    domain="d0.example.com",
                    active=1,
                )
            )
        for i in range(aliases):
            conn.execute(
                md.tables["alias"]
                .insert()
                .values(
                    address=f"a{i}@d0.example.com",
                    goto="u0@d0.example.com",
                    domain="d0.example.com",
                    active=1,
                )
            )
        for i in range(quotas):
            conn.execute(
                md.tables["quota2"]
                .insert()
                .values(username=f"u{i}@d0.example.com", bytes=0, messages=0)
            )


def test_snapshot_returns_zero_counts_on_empty_schema(db: Engine) -> None:
    md = MetaData()
    md.reflect(bind=db)
    report = StatusService(engine=db, metadata=md).snapshot()
    assert isinstance(report, StatusReport)
    assert report == StatusReport(domains=0, mailboxes=0, aliases=0, quota2=0)


def test_snapshot_counts_each_table(db: Engine) -> None:
    _seed(db, domains=1, mailboxes=3, aliases=2, quotas=3)
    md = MetaData()
    md.reflect(bind=db)
    report = StatusService(engine=db, metadata=md).snapshot()
    assert report.domains == 1
    assert report.mailboxes == 3
    assert report.aliases == 2
    assert report.quota2 == 3


def test_status_report_serialises_to_json() -> None:
    """Renderer's --json path round-trips StatusReport via Pydantic."""
    r = StatusReport(domains=1, mailboxes=2, aliases=3, quota2=2)
    payload = r.model_dump(mode="json")
    assert payload == {"domains": 1, "mailboxes": 2, "aliases": 3, "quota2": 2}
