"""ServicesBundle — wires the four services together for the CLI.

Built once at CLI startup from PostinoSettings + PostfixSqlCredentials.
Constructor injection through and through; no globals."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

from postino_core.config import PostinoSettings
from postino_core.db import make_engine, reflect_schema
from postino_core.fs import FilesystemAdapter
from postino_core.hooks import HookRunner
from postino_core.providers.base import IdentityProvider
from postino_core.providers.local import LocalProvider
from postino_core.services.alias import AliasService
from postino_core.services.domain import DomainService
from postino_core.services.mailbox import MailboxService
from postino_core.services.quota import QuotaService


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
        settings: PostinoSettings,
    ) -> None:
        self.engine = engine
        self.metadata = metadata
        self.identity = identity
        self.mailbox = mailbox
        self.alias = alias
        self.domain = domain
        self.quota = quota
        self.settings = settings


def build_services(
    settings: PostinoSettings,
    *,
    clock: Callable[[], datetime],
    echo: bool,
) -> ServicesBundle:
    """Construct a ServicesBundle from settings.

    Returns: a fully wired ServicesBundle. Caller owns engine disposal."""
    creds = settings.mailbox_creds()
    engine = make_engine(creds, echo=echo)
    metadata = reflect_schema(engine)
    identity = LocalProvider(metadata=metadata)
    fs = FilesystemAdapter(
        mail_root=settings.virtual_mailbox_base,
        vmail_uid=settings.vmail_uid,
        vmail_gid=settings.vmail_gid,
    )
    hooks = HookRunner(script_path=settings.postcreation_hook)
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
        ),
        alias=AliasService(engine=engine, metadata=metadata, clock=clock),
        domain=DomainService(engine=engine, metadata=metadata, clock=clock),
        quota=QuotaService(engine=engine, metadata=metadata),
        settings=settings,
    )
