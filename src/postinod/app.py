"""Litestar app factory.

The full DI wiring (settings-driven Engine, JWKS cache, identity
provider selection) lands in task 15. This file currently exposes:

* `build_app(*, ready_callback)` — minimal app with health endpoints,
  used by Task 3's tests and Task 4's guard tests.
* `build_app_for_test(...)` — test-only factory for the integration
  suite (Tasks 9, 12, 13). Takes pre-built dependencies (Engine,
  MetaData, HMAC secret, optional SCIM JWKS stub) and wires the
  Zitadel router, SCIM Users router, SCIM Aliases router, and health
  router. Production wiring lands in Task 15.
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
from postino_core.services.alias import AliasService
from postino_core.services.mailbox import MailboxService
from postinod.auth.hmac_guard import HmacVerifier
from postinod.auth.jwks import JwksCache
from postinod.auth.jwt_guard import JwksLike, JwtVerifier
from postinod.health import build_health_router
from postinod.scim.aliases import build_aliases_router
from postinod.scim.users import build_users_router
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
    scim_issuer: str = "https://idp.test",
    scim_audience: str = "postinod",
    jwks: JwksLike | None = None,
) -> Litestar:
    """Test-only Litestar app factory.

    Wires a `MailboxService` against the supplied test engine using the
    NoAuthProvider (postinod V2 ships with the IdP-owns-credentials
    contract; LocalProvider is for the postino CLI). Production wiring
    with settings-driven DI lands in Task 15.

    `mail_root` and `postcreation_hook` are required; callers (pytest
    fixtures) are responsible for temp-path lifecycle via tmp_path / tmp_path_factory.

    If `jwks` is provided, it is used for JWT verification. Otherwise a
    `JwksCache` pointing at `{scim_issuer}/.well-known/jwks.json` is used.
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
    alias_service = AliasService(engine=db_engine, metadata=metadata, clock=_utc_now)
    verifier = HmacVerifier(secret=hmac_secret)

    if jwks is None:
        jwks = JwksCache(
            jwks_url=f"{scim_issuer}/.well-known/jwks.json",
            refresh_seconds=3600,
        )

    jwt_verifier = JwtVerifier(
        issuer=scim_issuer,
        audience=scim_audience,
        jwks=jwks,
    )

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
            build_users_router(
                mailbox_service=mailbox,
                jwt_verifier=jwt_verifier,
                engine=db_engine,
                metadata=metadata,
                clock=_utc_now,
                default_quota_bytes=default_quota_bytes,
            ),
            build_aliases_router(
                alias_service=alias_service,
                jwt_verifier=jwt_verifier,
                engine=db_engine,
                metadata=metadata,
                clock=_utc_now,
            ),
        ],
        debug=False,
    )
