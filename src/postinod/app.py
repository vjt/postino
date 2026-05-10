"""Litestar app factory.

The full DI wiring (settings-driven Engine, JWKS cache, identity
provider selection) lands in task 15. This file currently exposes:

* `build_app(*, ready_callback)` — minimal app with health endpoints,
  used by Task 3's tests and Task 4's guard tests.
* `build_app_for_test(...)` — test-only factory for the integration
  suite (Tasks 9, 12). Takes pre-built dependencies (Engine, MetaData,
  HMAC secret, optional SCIM JWKS stub) and wires the Zitadel router,
  SCIM Users router, and health router. Production wiring lands in Task 15.
"""

from __future__ import annotations

import tempfile
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
from postinod.auth.jwks import JwksCache
from postinod.auth.jwt_guard import JwksLike, JwtVerifier
from postinod.health import build_health_router
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


class _StubJwks:
    """In-process JWKS stub for integration tests.

    Satisfies JwksLike; resolves kid lookups from a static dict of JWK
    objects passed at construction. KeyError surfaces to JwtVerifier → 401.
    """

    def __init__(self, keys: list[dict[str, object]]) -> None:
        self._by_kid: dict[str, dict[str, object]] = {str(k["kid"]): k for k in keys}

    async def get(self, kid: str) -> dict[str, object]:
        return self._by_kid[kid]  # KeyError → JwtVerifier → 401


def build_app_for_test(
    *,
    db_engine: Engine,
    metadata: MetaData,
    hmac_secret: bytes,
    mail_root: Path | None = None,
    postcreation_hook: Path | None = None,
    default_quota_bytes: int = DEFAULT_TEST_QUOTA_BYTES,
    scim_issuer: str = "https://idp.test",
    scim_audience: str = "postinod",
    jwks_stub_keys: list[dict[str, object]] | None = None,
) -> Litestar:
    """Test-only Litestar app factory.

    Wires a `MailboxService` against the supplied test engine using the
    NoAuthProvider (postinod V2 ships with the IdP-owns-credentials
    contract; LocalProvider is for the postino CLI). Production wiring
    with settings-driven DI lands in Task 15.

    `mail_root` and `postcreation_hook` are optional: if omitted, a
    temporary directory and a no-op hook script are created automatically
    so callers that only exercise the HTTP layer do not need to supply them.

    If `jwks_stub_keys` is provided, a `_StubJwks` is used for JWT
    verification (test-only, no HTTP fetches). Otherwise a `JwksCache`
    pointing at `{scim_issuer}/.well-known/jwks.json` is used.
    """
    # Resolve optional filesystem args.
    if mail_root is None:
        _tmpdir = tempfile.mkdtemp()
        mail_root = Path(_tmpdir)
    if postcreation_hook is None:
        with tempfile.NamedTemporaryFile(suffix=".sh", delete=False) as _f:
            _hook = Path(_f.name)
        _hook.write_text("#!/bin/sh\nexit 0\n")
        _hook.chmod(0o755)
        postcreation_hook = _hook

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

    jwks: JwksLike
    if jwks_stub_keys is not None:
        jwks = _StubJwks(jwks_stub_keys)
    else:
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
        ],
        debug=False,
    )
