from __future__ import annotations

import pytest
from litestar.testing import AsyncTestClient

from postinod.app import build_app


@pytest.fixture
async def client() -> AsyncTestClient:  # type: ignore[type-arg]  # WHY: AsyncTestClient is generic on the app type but test fixtures don't need the type param
    app = build_app(ready_callback=lambda: True)
    async with AsyncTestClient(app=app) as c:
        yield c  # type: ignore[misc]  # WHY: pytest-asyncio async generator fixtures require yield inside async with; mypy/pyright flag this but it works at runtime


async def test_healthz_returns_200(client: AsyncTestClient) -> None:  # type: ignore[type-arg]  # WHY: same as above
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_readyz_ready(client: AsyncTestClient) -> None:  # type: ignore[type-arg]  # WHY: same as above
    r = await client.get("/readyz")
    assert r.status_code == 200


async def test_readyz_not_ready() -> None:
    app = build_app(ready_callback=lambda: False)
    async with AsyncTestClient(app=app) as c:
        r = await c.get("/readyz")
        assert r.status_code == 503
