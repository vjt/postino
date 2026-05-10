"""JWKS cache: TTL expiry, force-refresh on unknown kid, stale-on-failure."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from postinod.auth.jwks import JwksCache

JWKS_URL = "https://idp.example.org/.well-known/jwks.json"


def _jwks(kid: str) -> dict[str, list[dict[str, str]]]:
    return {"keys": [{"kty": "RSA", "kid": kid, "n": "abc", "e": "AQAB", "use": "sig"}]}


@pytest.fixture
async def respx_mock() -> AsyncGenerator[respx.MockRouter, None]:
    with respx.mock(assert_all_called=False) as r:
        yield r


async def test_fetch_caches_keys(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(JWKS_URL).respond(json=_jwks("kid-1"))
    cache = JwksCache(jwks_url=JWKS_URL, refresh_seconds=3600)
    k1 = await cache.get("kid-1")
    k2 = await cache.get("kid-1")
    assert k1 is k2
    assert respx_mock.calls.call_count == 1


async def test_unknown_kid_forces_refresh(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(JWKS_URL)
    route.side_effect = [
        httpx.Response(200, json=_jwks("kid-old")),
        httpx.Response(200, json=_jwks("kid-new")),
    ]
    cache = JwksCache(jwks_url=JWKS_URL, refresh_seconds=3600)
    await cache.get("kid-old")
    k = await cache.get("kid-new")
    assert k["kid"] == "kid-new"
    assert respx_mock.calls.call_count == 2


async def test_unknown_kid_after_refresh_raises(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(JWKS_URL).respond(json=_jwks("kid-1"))
    cache = JwksCache(jwks_url=JWKS_URL, refresh_seconds=3600)
    with pytest.raises(KeyError):
        await cache.get("kid-missing")


async def test_ttl_triggers_refresh(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(JWKS_URL)
    route.side_effect = [
        httpx.Response(200, json=_jwks("kid-1")),
        httpx.Response(200, json=_jwks("kid-2")),
    ]
    now = datetime(2026, 5, 10, tzinfo=UTC)
    later = now + timedelta(seconds=120)
    times = iter([now, now, later, later])
    cache = JwksCache(jwks_url=JWKS_URL, refresh_seconds=60, clock=lambda: next(times))
    await cache.get("kid-1")
    await cache.get("kid-2")
    assert respx_mock.calls.call_count == 2


async def test_stale_cache_on_fetch_failure(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(JWKS_URL)
    route.side_effect = [
        httpx.Response(200, json=_jwks("kid-1")),
        httpx.ConnectError("boom"),
    ]
    # refresh_seconds=0 forces refresh on every get(); after first populates
    # cache, second triggers refresh, refresh fails, stale cache should still
    # serve kid-1.
    cache = JwksCache(jwks_url=JWKS_URL, refresh_seconds=0)
    await cache.get("kid-1")
    k = await cache.get("kid-1")
    assert k["kid"] == "kid-1"
