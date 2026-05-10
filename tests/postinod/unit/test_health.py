from __future__ import annotations

import collections.abc

import pytest
from litestar import Litestar
from litestar.testing import AsyncTestClient

from postinod.app import build_minimal_app


@pytest.fixture
async def client() -> collections.abc.AsyncGenerator[AsyncTestClient[Litestar], None]:
    app = build_minimal_app(ready_callback=lambda: True)
    async with AsyncTestClient(app=app) as c:
        yield c


async def test_healthz_returns_200(client: AsyncTestClient[Litestar]) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_readyz_ready(client: AsyncTestClient[Litestar]) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200


async def test_readyz_not_ready() -> None:
    app = build_minimal_app(ready_callback=lambda: False)
    async with AsyncTestClient(app=app) as c:
        r = await c.get("/readyz")
        assert r.status_code == 503
