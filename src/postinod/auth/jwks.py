"""JWKS fetcher with TTL cache.

Per spec §5.4:
* JWKS fetched from `${scim_issuer}/.well-known/jwks.json` at startup,
  cached for `scim_jwks_refresh_seconds`.
* Unknown `kid` → force-refresh once; still missing → KeyError (caller
  surfaces as 401).
* Refresh failure (IdP unreachable) → log error, keep stale cache, do
  NOT take service down.

The cache is shared across coroutines; concurrent kid lookups during
a refresh are serialised by an asyncio.Lock to avoid duplicate fetches
under load. Two coroutines that both observe `_needs_refresh()` before
either acquires the lock will fetch sequentially (the second fetch is
redundant but idempotent — JWKS rotation tolerates duplicate writes).
A double-checked-lock pattern would deduplicate but the burst window
is one async yield point; the cost isn't worth the complexity for the
SCIM auth surface's request volume.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

_logger = logging.getLogger(__name__)


class JwksCache:
    def __init__(
        self,
        *,
        jwks_url: str,
        refresh_seconds: int,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._url = jwks_url
        self._ttl = refresh_seconds
        self._client = client or httpx.AsyncClient(timeout=5.0)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._keys: dict[str, dict[str, object]] = {}
        self._fetched_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def get(self, kid: str) -> dict[str, object]:
        """Return the JWK for `kid` or raise KeyError.

        Refreshes the cache if TTL expired or kid is unknown. If the
        refresh fails AND we have a stale cache, serves stale; if the
        cache is empty, propagates the httpx error.
        """
        if self._needs_refresh() or kid not in self._keys:
            await self._refresh()
        if kid in self._keys:
            return self._keys[kid]
        raise KeyError(f"no JWK with kid={kid!r}")

    def _needs_refresh(self) -> bool:
        if self._fetched_at is None:
            return True
        age = (self._clock() - self._fetched_at).total_seconds()
        return age >= self._ttl

    async def _refresh(self) -> None:
        async with self._lock:
            try:
                resp = await self._client.get(self._url)
                resp.raise_for_status()
                data = resp.json()  # type: ignore[var-annotated]  # WHY: httpx.Response.json() returns Any; we narrow below
            except (httpx.HTTPError, ValueError) as e:
                _logger.error("jwks refresh failed: %s", e)
                if not self._keys:
                    raise
                return  # keep serving stale keys
            new_keys: dict[str, dict[str, object]] = {}
            # Coerce `{"keys": null}` (or missing key) to `[]` so a buggy IdP
            # doesn't bypass the stale-on-failure path with a naked TypeError.
            raw = data.get("keys")
            keys_iter: list[object] = raw if isinstance(raw, list) else []  # type: ignore[assignment]  # WHY: raw narrowed by isinstance; pyright still sees list[Any] as partially unknown — element validation happens per-iter below
            for k in keys_iter:
                if isinstance(k, dict) and "kid" in k:
                    new_keys[k["kid"]] = k  # type: ignore[assignment]  # WHY: k is dict[Any, Any] from JSON; narrowed by isinstance check, kid values are safe as dict[str, object]
            self._keys = new_keys
            self._fetched_at = self._clock()
