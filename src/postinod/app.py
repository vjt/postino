"""Litestar app factory.

The full DI wiring (Engine, services, JWKS cache, settings) lands in
task 15. This file currently exposes `build_app` returning a minimal
app with health endpoints — enough to drive task 3's tests and task 4's
guard tests in isolation.
"""

from __future__ import annotations

from collections.abc import Callable

from litestar import Litestar

from postinod.health import build_health_router


def build_app(*, ready_callback: Callable[[], bool]) -> Litestar:
    """Construct the Litestar app.

    `ready_callback` lets tests inject readiness state. In production
    (task 15) this becomes a closure over the DB ping + JWKS cache.
    """
    return Litestar(
        route_handlers=[build_health_router(ready_callback=ready_callback)],
        debug=False,
    )
