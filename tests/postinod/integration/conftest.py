"""Integration conftest for postinod.

Reuses the project-wide `db` engine fixture (defined in tests/conftest.py)
which already replays tests/fixtures/postfixadmin.sql and TRUNCATEs every
table per test. This conftest layers on top:

* exposes `prepared_test_db` as a (engine, metadata) bundle so tests can
  reflect once and reuse, mirroring how production wiring will pass them
  to the router (Task 15).
* seeds an `example.org` domain with capacity for the Zitadel-driven
  mailboxes the integration tests create.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class PreparedTestDB:
    engine: Engine
    metadata: MetaData


@pytest.fixture
def prepared_test_db(db: Engine) -> Iterator[PreparedTestDB]:
    """Engine + reflected metadata + seeded `example.org` domain.

    `db` (root conftest) yields a TRUNCATEd-per-test engine. We reflect
    once here and seed a single test domain with capacity 100.
    """
    md = MetaData()
    md.reflect(bind=db)
    domain = md.tables["domain"]
    with db.begin() as conn:
        conn.execute(
            domain.insert().values(
                domain="example.org",
                description="postinod integration tests",
                aliases=100,
                mailboxes=100,
                maxquota=0,
                quota=1073741824,
                transport="virtual",
                backupmx=0,
                active=1,
            )
        )
    yield PreparedTestDB(engine=db, metadata=md)
