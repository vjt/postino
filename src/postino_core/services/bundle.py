"""ServicesBundle — wires the four services together for the CLI.

Built once at CLI startup from PostinoSettings + PostfixSqlCredentials.
Constructor injection through and through; no globals."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

from postino_core.adapters.mlmmj import MlmmjAdapter
from postino_core.audit import AuditWriter, DefaultAuditWriter, default_actor
from postino_core.config import PostinoSettings
from postino_core.db import make_engine, reflect_schema
from postino_core.enums import IdentityBackend
from postino_core.errors import ConfigError
from postino_core.fs import FilesystemAdapter
from postino_core.hooks import HookRunner
from postino_core.providers import IdentityProvider, LocalProvider, NoAuthProvider
from postino_core.services.alias import AliasService
from postino_core.services.domain import DomainService
from postino_core.services.mailbox import MailboxService
from postino_core.services.mailing_list import MailingListService
from postino_core.services.quota import QuotaService
from postino_core.services.status import StatusService


class ServicesBundle:
    def __init__(
        self,
        *,
        engine: Engine,
        metadata: MetaData,
        identity: IdentityProvider,
        mailbox: MailboxService,
        alias: AliasService,
        domain: DomainService,
        quota: QuotaService,
        status: StatusService,
        mailing_list: MailingListService | None,
        settings: PostinoSettings,
    ) -> None:
        self.engine = engine
        self.metadata = metadata
        self.identity = identity
        self.mailbox = mailbox
        self.alias = alias
        self.domain = domain
        self.quota = quota
        self.status = status
        self.mailing_list = mailing_list
        self.settings = settings


def build_services(
    settings: PostinoSettings,
    *,
    clock: Callable[[], datetime],
    echo: bool,
    actor: Callable[[], str] | None = None,
    audit_writer_factory: Callable[[MetaData], AuditWriter] | None = None,
) -> ServicesBundle:
    """Construct a ServicesBundle from settings.

    `actor` is the callable that resolves the audit-log username when the
    default writer is used; the CLI passes ``getpass.getuser`` (kept
    outside `postino_core` so the core layer never imports getpass).

    `audit_writer_factory` overrides the default `DefaultAuditWriter` —
    postinod passes a factory that builds a `PostinodAuditWriter` so
    `postino.*` and `postinod.*` rows commit atomically with the
    mutation transaction. The factory receives the just-reflected
    MetaData so it can bind to the same `log` table.

    Returns: a fully wired ServicesBundle. Caller owns engine disposal.
    """
    creds = settings.mailbox_creds()
    engine = make_engine(creds, echo=echo)
    metadata = reflect_schema(engine)
    identity = _provider_for(settings.identity_backend, metadata=metadata, clock=clock)
    fs = FilesystemAdapter(
        mail_root=settings.virtual_mailbox_base,
        vmail_uid=settings.vmail_uid,
        vmail_gid=settings.vmail_gid,
    )
    hooks = HookRunner(
        script_path=settings.postcreation_hook,
        timeout=settings.postcreation_hook_timeout,
    )
    writer: AuditWriter = (
        audit_writer_factory(metadata)
        if audit_writer_factory is not None
        else DefaultAuditWriter(
            metadata=metadata,
            clock=clock,
            actor=actor or default_actor,
        )
    )
    mailing_list: MailingListService | None = None
    if settings.mlmmj_spool_dir is not None:
        adapter = MlmmjAdapter(
            spool_root=settings.mlmmj_spool_dir,
            mlmmj_uid=settings.mlmmj_uid,
            mlmmj_gid=settings.mlmmj_gid,
        )
        mailing_list = MailingListService(
            engine=engine,
            metadata=metadata,
            adapter=adapter,
            clock=clock,
            audit_writer=writer,
        )
    return ServicesBundle(
        engine=engine,
        metadata=metadata,
        identity=identity,
        mailbox=MailboxService(
            engine=engine,
            identity=identity,
            fs=fs,
            hooks=hooks,
            clock=clock,
            metadata=metadata,
            audit_writer=writer,
        ),
        alias=AliasService(engine=engine, metadata=metadata, clock=clock, audit_writer=writer),
        domain=DomainService(
            engine=engine,
            metadata=metadata,
            clock=clock,
            fs=fs,
            lmtp_destination=settings.lmtp_destination,
            audit_writer=writer,
        ),
        quota=QuotaService(engine=engine, metadata=metadata),
        status=StatusService(engine=engine, metadata=metadata),
        mailing_list=mailing_list,
        settings=settings,
    )


def _provider_for(
    backend: IdentityBackend,
    *,
    metadata: MetaData,
    clock: Callable[[], datetime],
) -> IdentityProvider:
    """Map IdentityBackend → IdentityProvider implementation.

    The PostinoSettings validator already rejects unknown values, so the
    fallback is defence-in-depth against future enum additions that
    forget to extend this dispatch.
    """
    if backend is IdentityBackend.LOCAL:
        return LocalProvider(metadata=metadata, clock=clock)
    if backend is IdentityBackend.NOAUTH:
        return NoAuthProvider()
    raise ConfigError(f"no IdentityProvider implementation for backend {backend.value!r}")
