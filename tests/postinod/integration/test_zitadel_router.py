"""Zitadel router integration tests — hit a real MariaDB via POSTINO_TEST_DB_URL.

Five scenarios (spec §3.6):
1. user.human.added → mailbox row created with {NOAUTH} sentinel.
2. unknown domain in email → 400 (NotFoundError mapping).
3. user.human.added then user.deactivated → mailbox.active flips to 0.
4. unknown event_type → 200 OK no-op (IGNORE outcome).
5. successful CREATE writes a `postinod.user.create` audit row to PA's `log`.
"""

from __future__ import annotations

import collections.abc
import hashlib
import hmac
import json
from pathlib import Path

import pytest
from litestar import Litestar
from litestar.testing import AsyncTestClient
from sqlalchemy import select

from .conftest import PreparedTestDB

pytestmark = pytest.mark.integration


def _sign(secret: bytes, body: bytes) -> str:
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


@pytest.fixture
def hmac_secret() -> bytes:
    return b"test-hmac-secret"


@pytest.fixture
async def client(
    prepared_test_db: PreparedTestDB,
    hmac_secret: bytes,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> collections.abc.AsyncGenerator[AsyncTestClient[Litestar], None]:
    """Build a fully-wired postinod app pointing at the test DB."""
    from postinod.app import build_app_for_test

    mail_root = tmp_path / "vmail"
    mail_root.mkdir()
    app = build_app_for_test(
        db_engine=prepared_test_db.engine,
        metadata=prepared_test_db.metadata,
        hmac_secret=hmac_secret,
        mail_root=mail_root,
        postcreation_hook=fake_postcreation_hook,
    )
    async with AsyncTestClient(app=app) as c:
        yield c


async def test_user_added_creates_mailbox(
    client: AsyncTestClient[Litestar],
    hmac_secret: bytes,
    prepared_test_db: PreparedTestDB,
) -> None:
    body = json.dumps(
        {
            "aggregateID": "agg-1",
            "userID": "user-1",
            "event_type": "user.human.added",
            "created_at": "2026-05-10T11:00:00Z",
            "event_payload": {
                "email": "alice@example.org",
                "firstName": "Alice",
                "lastName": "Rossi",
                "active": True,
            },
        }
    ).encode()
    r = await client.post(
        "/zitadel/events",
        content=body,
        headers={
            "ZITADEL-Signature": _sign(hmac_secret, body),
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    mailbox = prepared_test_db.metadata.tables["mailbox"]
    with prepared_test_db.engine.connect() as conn:
        row = conn.execute(
            select(mailbox).where(mailbox.c.username == "alice@example.org")
        ).fetchone()
    assert row is not None
    assert row.password == "{NOAUTH}"


async def test_unknown_domain_returns_400(
    client: AsyncTestClient[Litestar], hmac_secret: bytes
) -> None:
    body = json.dumps(
        {
            "aggregateID": "a",
            "userID": "u",
            "event_type": "user.human.added",
            "created_at": "2026-05-10T11:00:00Z",
            "event_payload": {
                "email": "bob@nope.invalid",
                "firstName": "B",
                "lastName": "B",
                "active": True,
            },
        }
    ).encode()
    r = await client.post(
        "/zitadel/events",
        content=body,
        headers={
            "ZITADEL-Signature": _sign(hmac_secret, body),
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 400


async def test_deactivate_then_check_status(
    client: AsyncTestClient[Litestar],
    hmac_secret: bytes,
    prepared_test_db: PreparedTestDB,
) -> None:
    create = json.dumps(
        {
            "aggregateID": "a",
            "userID": "u",
            "event_type": "user.human.added",
            "created_at": "2026-05-10T11:00:00Z",
            "event_payload": {
                "email": "carol@example.org",
                "firstName": "Carol",
                "lastName": "X",
                "active": True,
            },
        }
    ).encode()
    r1 = await client.post(
        "/zitadel/events",
        content=create,
        headers={"ZITADEL-Signature": _sign(hmac_secret, create)},
    )
    assert r1.status_code == 200, r1.text

    deact = json.dumps(
        {
            "aggregateID": "a",
            "userID": "u",
            "event_type": "user.deactivated",
            "created_at": "2026-05-10T11:01:00Z",
            "event_payload": {"email": "carol@example.org"},
        }
    ).encode()
    r2 = await client.post(
        "/zitadel/events",
        content=deact,
        headers={"ZITADEL-Signature": _sign(hmac_secret, deact)},
    )
    assert r2.status_code == 200, r2.text

    mailbox = prepared_test_db.metadata.tables["mailbox"]
    with prepared_test_db.engine.connect() as conn:
        row = conn.execute(
            select(mailbox).where(mailbox.c.username == "carol@example.org")
        ).fetchone()
    assert row is not None
    assert row.active == 0


async def test_unknown_event_returns_200_no_op(
    client: AsyncTestClient[Litestar], hmac_secret: bytes
) -> None:
    body = json.dumps(
        {
            "aggregateID": "a",
            "userID": "u",
            "event_type": "user.something.unknown",
            "created_at": "2026-05-10T11:00:00Z",
            "event_payload": {},
        }
    ).encode()
    r = await client.post(
        "/zitadel/events",
        content=body,
        headers={"ZITADEL-Signature": _sign(hmac_secret, body)},
    )
    assert r.status_code == 200


async def test_audit_row_written(
    client: AsyncTestClient[Litestar],
    hmac_secret: bytes,
    prepared_test_db: PreparedTestDB,
) -> None:
    body = json.dumps(
        {
            "aggregateID": "a",
            "userID": "u-dave",
            "event_type": "user.human.added",
            "created_at": "2026-05-10T11:00:00Z",
            "event_payload": {
                "email": "dave@example.org",
                "firstName": "D",
                "lastName": "D",
                "active": True,
            },
        }
    ).encode()
    r = await client.post(
        "/zitadel/events",
        content=body,
        headers={"ZITADEL-Signature": _sign(hmac_secret, body)},
    )
    assert r.status_code == 200, r.text

    log = prepared_test_db.metadata.tables["log"]
    with prepared_test_db.engine.connect() as conn:
        rows = conn.execute(select(log).where(log.c.action == "postinod.user.create")).fetchall()
    assert len(rows) >= 1
