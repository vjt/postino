"""JWKS fetcher with TTL cache, hardened against IdP outages and kid floods.

Per spec §5.4 plus v0.4 hardening (Task 1, A4.7/A4.8):

* JWKS fetched from `${scim_issuer}/.well-known/jwks.json` at startup,
  cached for `refresh_seconds`.
* Unknown `kid` → force-refresh once, gated by a per-kid cooldown so a
  flood of bogus kids cannot turn the cache into a load generator on the
  IdP. The cooldown timer per kid resets every miss; the miss map is
  bounded (FIFO trim) to avoid unbounded growth.
* TTL-expired-but-cache-non-empty → serve stale on fetch failure UNTIL
  `stale_serve_max_seconds` has elapsed past the TTL. Past that boundary,
  propagate the fetch error rather than authenticating against keys
  whose freshness we can no longer vouch for.
* Empty cache → propagate fetch errors. Refusing to start is preferable
  to silently accepting unsigned-equivalent tokens.

The cache is shared across coroutines; concurrent kid lookups during a
refresh are serialised by an `asyncio.Lock` to avoid duplicate fetches
under load. A double-checked-lock pattern would deduplicate exactly,
but the burst window is one async yield point; for the SCIM auth
surface's request volume the simpler form is correct.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

_logger = logging.getLogger(__name__)

_DEFAULT_UNKNOWN_KID_COOLDOWN_SEC = 30
_DEFAULT_STALE_SERVE_MAX_SEC = 86400
_DEFAULT_MAX_KID_MISS_ENTRIES = 1000


class JwksCache:
    def __init__(
        self,
        *,
        jwks_url: str,
        refresh_seconds: int,
        unknown_kid_cooldown_seconds: int = _DEFAULT_UNKNOWN_KID_COOLDOWN_SEC,
        stale_serve_max_seconds: int = _DEFAULT_STALE_SERVE_MAX_SEC,
        max_kid_miss_entries: int = _DEFAULT_MAX_KID_MISS_ENTRIES,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._url = jwks_url
        self._ttl = refresh_seconds
        self._unknown_kid_cooldown = unknown_kid_cooldown_seconds
        self._stale_serve_max = stale_serve_max_seconds
        self._max_kid_miss = max_kid_miss_entries
        self._client = client or httpx.AsyncClient(timeout=5.0)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._keys: dict[str, dict[str, object]] = {}
        self._fetched_at: datetime | None = None
        self._kid_miss_at: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def get(self, kid: str) -> dict[str, object]:
        """Return the JWK for `kid` or raise KeyError.

        Refresh policy:
          1. Empty cache → refresh, propagate errors (no stale fallback).
          2. TTL expired → refresh; on failure serve stale ONLY while
             age < ttl + stale_serve_max; past that, propagate.
          3. Kid known after refresh → return.
          4. Unknown kid + within per-kid cooldown → raise without
             refresh (stops flood-driven IdP hammering).
          5. Unknown kid + outside cooldown → one forced refresh; if
             kid still missing, record cooldown timestamp and raise.
        """
        if self._fetched_at is None:
            await self._refresh(stale_ok=False)
        elif self._needs_refresh():
            stale_ok = not self._past_max_stale()
            await self._refresh(stale_ok=stale_ok)

        if kid in self._keys:
            self._kid_miss_at.pop(kid, None)
            return self._keys[kid]

        if self._in_unknown_kid_cooldown(kid):
            raise KeyError(f"no JWK with kid={kid!r} (cooldown active)")

        await self._refresh(stale_ok=True)
        if kid in self._keys:
            self._kid_miss_at.pop(kid, None)
            return self._keys[kid]

        self._record_unknown_kid_miss(kid)
        raise KeyError(f"no JWK with kid={kid!r}")

    def _needs_refresh(self) -> bool:
        if self._fetched_at is None:
            return True
        age = (self._clock() - self._fetched_at).total_seconds()
        return age >= self._ttl

    def _past_max_stale(self) -> bool:
        if self._fetched_at is None:
            return False
        age = (self._clock() - self._fetched_at).total_seconds()
        return age >= self._ttl + self._stale_serve_max

    def _in_unknown_kid_cooldown(self, kid: str) -> bool:
        miss_at = self._kid_miss_at.get(kid)
        if miss_at is None:
            return False
        age = (self._clock() - miss_at).total_seconds()
        return age < self._unknown_kid_cooldown

    def _record_unknown_kid_miss(self, kid: str) -> None:
        # FIFO trim: dict preserves insertion order; drop the oldest miss
        # so a flood of distinct unknown kids cannot grow the map without
        # bound.
        if kid not in self._kid_miss_at and len(self._kid_miss_at) >= self._max_kid_miss:
            oldest_kid = next(iter(self._kid_miss_at))
            del self._kid_miss_at[oldest_kid]
        self._kid_miss_at[kid] = self._clock()

    async def _refresh(self, *, stale_ok: bool) -> None:
        async with self._lock:
            try:
                resp = await self._client.get(self._url)
                resp.raise_for_status()
                data = resp.json()  # type: ignore[var-annotated]  # WHY: httpx.Response.json() returns Any; we narrow below
            except (httpx.HTTPError, ValueError) as e:
                _logger.error("jwks refresh failed: %s", e)
                if not self._keys or not stale_ok:
                    raise
                return  # keep serving stale keys within window
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
