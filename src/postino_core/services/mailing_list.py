"""MailingListService — owns the contract between PostfixAdmin's
``domain`` table and the mlmmj spool tree.

State of record: the filesystem (spool dir + ``control/owner`` +
``subscribers.d/``). The PA ``domain`` row for ``lists.<domain>`` is
the only DB write postino performs to make routing work, and that
goes through DomainService, not here.

This service writes one row per mutation to the ``log`` audit table
(action namespace ``postino.mailing_list.<verb>``) so admins inspecting
the PA web UI see CLI list operations alongside web-UI mailbox/alias
mutations."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from pydantic import EmailStr
from sqlalchemy import MetaData, select
from sqlalchemy.engine import Connection, Engine

from postino_core.adapters.mlmmj import MlmmjAdapter
from postino_core.audit import mk_action, write_audit
from postino_core.db import translate_db_errors
from postino_core.errors import (
    AlreadyExistsError,
    CapacityError,
    ConfigError,
    NotFoundError,
)
from postino_core.models import MailingList, MailingListCreate

_logger = logging.getLogger(__name__)


class MailingListService:
    def __init__(
        self,
        *,
        engine: Engine,
        metadata: MetaData,
        adapter: MlmmjAdapter,
        clock: Callable[[], datetime],
    ) -> None:
        self._engine = engine
        self._md = metadata
        self._adapter = adapter
        self._clock = clock

    def add(self, create: MailingListCreate) -> MailingList:
        """Create a new mlmmj mailing list.

        Returns: the freshly-read MailingList.
        Raises:
            ConfigError: domain transport != 'mlmmj' or domain absent.
            AlreadyExistsError: address collides with mailbox/alias/list.
            MlmmjError, FilesystemError: from the adapter.
        """
        _, _, domain = str(create.address).partition("@")
        with translate_db_errors(), self._engine.connect() as conn:
            self._validate_domain_is_mlmmj(conn, domain)
            self._validate_no_collision(conn, str(create.address))

        # Spool tree first; on failure between adapter.create and audit-row
        # write we run adapter.delete() to roll back.
        self._adapter.create(address=create.address, primary_owner=create.owners[0])
        try:
            for owner in create.owners[1:]:
                self._adapter.append_owner(address=create.address, owner=owner)
            with translate_db_errors(), self._engine.begin() as conn:
                write_audit(
                    conn,
                    self._md,
                    clock=self._clock,
                    action=mk_action("mailing_list", "create"),
                    domain=domain,
                    data=str(create.address),
                )
        except Exception:
            try:
                self._adapter.delete(address=create.address)
            except Exception as compensation_err:
                _logger.error(
                    "compensation: adapter.delete(%s) failed after partial create: %s",
                    create.address,
                    compensation_err,
                )
            raise

        ml = self._adapter.get(address=create.address)
        if ml is None:
            raise ConfigError(
                f"mailing list {create.address} vanished after adapter.create — check spool perms"
            )
        return ml

    def subscribe(self, *, address: EmailStr, email: EmailStr) -> None:
        """Subscribe ``email`` to the list. Idempotent (mlmmj-sub -f)."""
        _, _, domain = str(address).partition("@")
        self._adapter.subscribe(address=address, email=email)
        with translate_db_errors(), self._engine.begin() as conn:
            write_audit(
                conn,
                self._md,
                clock=self._clock,
                action=mk_action("mailing_list", "subscribe"),
                domain=domain,
                data=f"{address} {email}",
            )

    def unsubscribe(self, *, address: EmailStr, email: EmailStr) -> None:
        """Unsubscribe ``email`` from the list. Idempotent."""
        _, _, domain = str(address).partition("@")
        self._adapter.unsubscribe(address=address, email=email)
        with translate_db_errors(), self._engine.begin() as conn:
            write_audit(
                conn,
                self._md,
                clock=self._clock,
                action=mk_action("mailing_list", "unsubscribe"),
                domain=domain,
                data=f"{address} {email}",
            )

    def get(self, address: EmailStr) -> MailingList | None:
        """Pure read; returns None if list does not exist."""
        return self._adapter.get(address=address)

    def delete(self, address: EmailStr, *, force: bool = False) -> None:
        """Delete a mailing list.

        Refuses (CapacityError) if ``subscriber_count > 0`` and ``force`` is False.
        Raises NotFoundError if the list spool dir does not exist.
        """
        _, _, domain = str(address).partition("@")
        ml = self._adapter.get(address=address)
        if ml is None:
            raise NotFoundError(f"mailing list {address!r} does not exist")
        if ml.subscriber_count > 0 and not force:
            raise CapacityError(
                f"mailing list {address!r} has {ml.subscriber_count} subscribers; "
                f"pass --force to delete anyway"
            )
        self._adapter.delete(address=address)
        with translate_db_errors(), self._engine.begin() as conn:
            write_audit(
                conn,
                self._md,
                clock=self._clock,
                action=mk_action("mailing_list", "delete"),
                domain=domain,
                data=f"{address} force={force}",
            )

    def _validate_domain_is_mlmmj(self, conn: Connection, domain: str) -> None:
        d = self._md.tables["domain"]
        row = conn.execute(select(d.c.transport).where(d.c.domain == domain)).fetchone()
        if row is None:
            raise ConfigError(f"domain {domain!r} does not exist")
        if str(row[0]) != "mlmmj":
            raise ConfigError(
                f"domain {domain!r} has transport={row[0]!r}, "
                f"expected 'mlmmj'. Run `postino domain add` with --transport mlmmj first."
            )

    def _validate_no_collision(self, conn: Connection, address: str) -> None:
        mailbox = self._md.tables["mailbox"]
        alias = self._md.tables["alias"]
        if (
            conn.execute(select(mailbox.c.username).where(mailbox.c.username == address)).fetchone()
            is not None
        ):
            raise AlreadyExistsError(f"mailbox row already exists for {address!r}")
        if (
            conn.execute(select(alias.c.address).where(alias.c.address == address)).fetchone()
            is not None
        ):
            raise AlreadyExistsError(f"alias row already exists for {address!r}")
        if self._adapter.get(address=address) is not None:  # type: ignore[arg-type]  # WHY: adapter.get accepts EmailStr; address is a validated str at the boundary
            raise AlreadyExistsError(f"mailing list {address!r} already exists")
