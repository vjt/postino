"""Health endpoints — no auth, no DB write.

* /healthz: process-alive, always 200.
* /readyz: caller-supplied readiness callback (DB ping + JWKS in app.py).
"""

from __future__ import annotations

from collections.abc import Callable

from litestar import Router, get
from litestar.exceptions import HTTPException


def build_health_router(*, ready_callback: Callable[[], bool]) -> Router:
    @get("/healthz", sync_to_thread=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @get("/readyz", sync_to_thread=False)
    def readyz() -> dict[str, str]:
        if not ready_callback():
            raise HTTPException(status_code=503, detail="not ready")
        return {"status": "ready"}

    return Router(path="/", route_handlers=[healthz, readyz])
