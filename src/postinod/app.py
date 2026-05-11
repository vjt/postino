"""Litestar app factory.

Exposes:

* ``build_app(*, toml_path)`` — production factory. Reads PostinoSettings +
  PostinodSettings from ``toml_path``, builds the services bundle, wires
  HMAC + JWT verifiers, JWKS cache, all routers.
* ``build_minimal_app(*, ready_callback)`` — minimal app with health endpoints
  only, used by the Task 3 health unit tests.
* ``build_app_for_test(...)`` — test-only factory for the integration suite
  (Tasks 9, 12, 13). Takes pre-built dependencies (Engine, MetaData, HMAC
  secret, optional SCIM JWKS stub) and wires all routers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from litestar import Litestar
from sqlalchemy import MetaData, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from postino_core.config import load_postino_settings
from postino_core.errors import ConfigError
from postino_core.fs import FilesystemAdapter
from postino_core.hooks import HookRunner
from postino_core.providers import NoAuthProvider
from postino_core.services.alias import AliasService
from postino_core.services.bundle import build_services
from postino_core.services.domain import DomainService
from postino_core.services.mailbox import MailboxService
from postinod.audit import PostinodAuditWriter
from postinod.auth.hmac_guard import HmacVerifier
from postinod.auth.jwks import JwksCache
from postinod.auth.jwt_guard import JwksLike, JwtVerifier
from postinod.config import (
    load_postinod_settings,
    read_zitadel_hmac_secrets,
    read_zitadel_replay_window_sec,
)
from postinod.health import build_health_router
from postinod.scim.aliases import build_aliases_router
from postinod.scim.discovery import build_discovery_router
from postinod.scim.domains import build_domains_router
from postinod.scim.users import build_users_router
from postinod.zitadel.events import build_zitadel_router

_logger = logging.getLogger(__name__)

DEFAULT_TEST_QUOTA_BYTES = 1073741824  # 1 GiB


def build_minimal_app(*, ready_callback: Callable[[], bool]) -> Litestar:
    """Minimal Litestar app with health endpoints only.

    Used by the Task 3 health unit tests. ``ready_callback`` lets tests
    inject readiness state without standing up the full DI graph.
    """
    return Litestar(
        route_handlers=[build_health_router(ready_callback=ready_callback)],
        debug=False,
    )


def build_app(*, toml_path: Path) -> Litestar:
    """Production app factory — reads PostinoSettings + PostinodSettings from ``toml_path``."""
    postino_settings = load_postino_settings(toml_path)
    postinod_settings = load_postinod_settings(toml_path)
    hmac_secrets = read_zitadel_hmac_secrets()
    replay_window = read_zitadel_replay_window_sec()

    bundle = build_services(
        postino_settings,
        clock=_utc_now,
        echo=False,
        audit_writer_factory=lambda md: PostinodAuditWriter(metadata=md, clock=_utc_now),
    )
    _assert_noauth_identity(bundle.identity)

    hmac_verifier = HmacVerifier(secrets=hmac_secrets)
    jwks = JwksCache(
        jwks_url=f"{postinod_settings.scim_issuer}/.well-known/jwks.json",
        refresh_seconds=postinod_settings.scim_jwks_refresh_seconds,
    )
    jwt_verifier = JwtVerifier(
        issuer=postinod_settings.scim_issuer,
        audience=postinod_settings.scim_audience,
        jwks=jwks,
    )

    def _ready() -> bool:
        try:
            with bundle.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except SQLAlchemyError as e:
            _logger.warning("readiness DB ping failed: %s", e)
            return False
        return True

    return Litestar(
        route_handlers=[
            build_health_router(ready_callback=_ready),
            build_zitadel_router(
                mailbox_service=bundle.mailbox,
                hmac_verifier=hmac_verifier,
                engine=bundle.engine,
                metadata=bundle.metadata,
                clock=_utc_now,
                default_quota_bytes=postino_settings.default_quota_bytes,
                replay_window_seconds=replay_window,
            ),
            build_users_router(
                mailbox_service=bundle.mailbox,
                jwt_verifier=jwt_verifier,
                default_quota_bytes=postino_settings.default_quota_bytes,
            ),
            build_aliases_router(
                alias_service=bundle.alias,
                jwt_verifier=jwt_verifier,
            ),
            build_domains_router(
                domain_service=bundle.domain,
                jwt_verifier=jwt_verifier,
            ),
            build_discovery_router(jwt_verifier=jwt_verifier),
        ],
        debug=False,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _assert_noauth_identity(identity: object) -> None:
    """Fail-fast guard: postinod refuses to wire any non-NoAuth provider.

    The daemon's contract is that an external IdP owns credentials and
    Dovecot's passdb chain verifies them. Booting with LocalProvider
    would silently expose mailbox.password to SCIM/Zitadel writes —
    making the IdP-owned-credentials promise unenforceable.
    """
    if not isinstance(identity, NoAuthProvider):
        raise ConfigError(
            "postinod requires identity_backend=noauth in postino.toml "
            f"(got {type(identity).__name__})"
        )


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
    replay_window_seconds: int = 86400,
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
    audit_writer = PostinodAuditWriter(metadata=metadata, clock=_utc_now)
    identity = NoAuthProvider()
    _assert_noauth_identity(identity)
    mailbox = MailboxService(
        engine=db_engine,
        identity=identity,
        fs=fs,
        hooks=hooks,
        clock=_utc_now,
        metadata=metadata,
        audit_writer=audit_writer,
    )
    alias_service = AliasService(
        engine=db_engine, metadata=metadata, clock=_utc_now, audit_writer=audit_writer
    )
    domain_service = DomainService(
        engine=db_engine,
        metadata=metadata,
        clock=_utc_now,
        fs=fs,
        lmtp_destination="localhost:24",
        audit_writer=audit_writer,
    )
    verifier = HmacVerifier(secrets=(hmac_secret,))

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
                replay_window_seconds=replay_window_seconds,
            ),
            build_users_router(
                mailbox_service=mailbox,
                jwt_verifier=jwt_verifier,
                default_quota_bytes=default_quota_bytes,
            ),
            build_aliases_router(
                alias_service=alias_service,
                jwt_verifier=jwt_verifier,
            ),
            build_domains_router(
                domain_service=domain_service,
                jwt_verifier=jwt_verifier,
            ),
            build_discovery_router(jwt_verifier=jwt_verifier),
        ],
        debug=False,
    )
