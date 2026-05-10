"""Litestar app factory.

The full DI wiring (settings-driven Engine, JWKS cache, identity
provider selection) lands in task 15. This file currently exposes:

* `build_app(*, ready_callback)` — minimal app with health endpoints,
  used by Task 3's tests and Task 4's guard tests.
* `build_app_for_test(...)` — test-only factory for the integration
  suite (Task 9). Takes pre-built dependencies (Engine, MetaData, HMAC
  secret) and wires the Zitadel router + health router. Production
  wiring lands in Task 15.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from litestar import Litestar
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

from postino_core.fs import FilesystemAdapter
from postino_core.hooks import HookRunner
from postino_core.providers import NoAuthProvider
from postino_core.services.mailbox import MailboxService
from postinod.auth.hmac_guard import HmacVerifier
from postinod.health import build_health_router
from postinod.zitadel.events import build_zitadel_router

DEFAULT_TEST_QUOTA_BYTES = 1073741824  # 1 GiB


def build_app(*, ready_callback: Callable[[], bool]) -> Litestar:
    """Construct the Litestar app.

    `ready_callback` lets tests inject readiness state. In production
    (task 15) this becomes a closure over the DB ping + JWKS cache.
    """
    return Litestar(
        route_handlers=[build_health_router(ready_callback=ready_callback)],
        debug=False,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def build_app_for_test(
    *,
    db_engine: Engine,
    metadata: MetaData,
    hmac_secret: bytes,
    mail_root: Path,
    postcreation_hook: Path,
    default_quota_bytes: int = DEFAULT_TEST_QUOTA_BYTES,
) -> Litestar:
    """Test-only Litestar app factory.

    Wires a `MailboxService` against the supplied test engine using the
    NoAuthProvider (postinod V2 ships with the IdP-owns-credentials
    contract; LocalProvider is for the postino CLI). Production wiring
    with settings-driven DI lands in Task 15.
    """
    fs = FilesystemAdapter(mail_root=mail_root, vmail_uid=-1, vmail_gid=-1)
    hooks = HookRunner(script_path=postcreation_hook)
    mailbox = MailboxService(
        engine=db_engine,
        identity=NoAuthProvider(),
        fs=fs,
        hooks=hooks,
        clock=_utc_now,
        metadata=metadata,
    )
    verifier = HmacVerifier(secret=hmac_secret)

    return Litestar(
        route_handlers=[
            build_zitadel_router(
                mailbox_service=mailbox,
                hmac_verifier=verifier,
                engine=db_engine,
                metadata=metadata,
                clock=_utc_now,
                default_quota_bytes=default_quota_bytes,
            ),
        ],
        debug=False,
    )
