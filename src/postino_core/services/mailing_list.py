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
from postino_core.audit import AuditWriter, DefaultAuditWriter, mk_action, sanitize_audit_error
from postino_core.db import translate_db_errors
from postino_core.errors import (
    AlreadyExistsError,
    CapacityError,
    ConfigError,
    NotFoundError,
)
from postino_core.models import MailingList, MailingListCreate
from postino_core.repos.routes import RoutesRepository

_logger = logging.getLogger(__name__)


class MailingListService:
    def __init__(
        self,
        *,
        engine: Engine,
        metadata: MetaData,
        adapter: MlmmjAdapter,
        routes: RoutesRepository,
        clock: Callable[[], datetime],
        audit_writer: AuditWriter | None = None,
    ) -> None:
        self._engine = engine
        self._md = metadata
        self._adapter = adapter
        self._routes = routes
        self._clock = clock
        self._audit: AuditWriter = audit_writer or DefaultAuditWriter(
            metadata=metadata, clock=clock
        )

    def add(self, create: MailingListCreate) -> MailingList:
        """Create a new mlmmj mailing list.

        Returns: the freshly-read MailingList.
        Raises:
            AlreadyExistsError: address collides with mailbox/alias/list.
            MlmmjError, FilesystemError: from the adapter.
        """
        _, _, domain = str(create.address).partition("@")
        try:
            with translate_db_errors(), self._engine.begin() as conn:
                self._validate_no_collision(conn, str(create.address))
                # DB writes first; if the adapter raises, the tx rolls back
                # and compensation below deletes any partial spool tree.
                self._routes.insert_mlmmj_list(conn, create.address)
                self._write_owner_alias(conn, str(create.address), list(create.owners))
                self._adapter.create(address=create.address, primary_owner=create.owners[0])
                for owner in create.owners[1:]:
                    self._adapter.append_owner(address=create.address, owner=owner)
                self._audit.write(
                    conn,
                    action=mk_action("mailing_list", "create"),
                    domain=domain,
                    data=str(create.address),
                )
        except Exception:
            # On any failure (validation, adapter, audit) the DB tx
            # has rolled back; compensate the FS spool tree if
            # adapter.create got far enough to leave one on disk.
            try:
                self._adapter.delete(address=create.address)
            except NotFoundError:
                pass  # adapter.create never got far enough — nothing to undo
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
            self._audit.write(
                conn,
                action=mk_action("mailing_list", "subscribe"),
                domain=domain,
                data=f"{address} {email}",
            )

    def unsubscribe(self, *, address: EmailStr, email: EmailStr) -> None:
        """Unsubscribe ``email`` from the list. Idempotent."""
        _, _, domain = str(address).partition("@")
        self._adapter.unsubscribe(address=address, email=email)
        with translate_db_errors(), self._engine.begin() as conn:
            self._audit.write(
                conn,
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

        FS-first ordering: ``adapter.delete`` removes the spool tree, then
        an audit row commits. A phantom-delete audit row whose spool tree
        still exists is misleading (admins trust audit). Failure of the FS
        removal leaves both FS + audit untouched — safe to retry.

        If the audit write fails *after* the spool has been removed, the
        operation has succeeded but the log row is missing; we emit a
        side-channel ``postino.mailing_list.audit_dropped`` row in a fresh
        transaction so the gap is surfaced to admins instead of silently
        dropped.
        """
        _, _, domain = str(address).partition("@")
        if force:
            if not self._adapter.exists(address=address):  # type: ignore[arg-type]  # WHY: adapter accepts EmailStr; address is a validated str at the boundary
                raise NotFoundError(f"mailing list {address!r} does not exist")
        else:
            ml = self._adapter.get(address=address)
            if ml is None:
                raise NotFoundError(f"mailing list {address!r} does not exist")
            if ml.subscriber_count > 0:
                raise CapacityError(
                    f"mailing list {address!r} has {ml.subscriber_count} subscribers; "
                    f"pass --force to delete anyway"
                )

        # FS-first: if rmtree fails, nothing is written; the caller can retry.
        self._adapter.delete(address=address)

        try:
            with translate_db_errors(), self._engine.begin() as conn:
                self._routes.delete_by_list_address(conn, address)
                self._delete_owner_alias(conn, str(address))
                self._audit.write(
                    conn,
                    action=mk_action("mailing_list", "delete"),
                    domain=domain,
                    data=f"{address} force={force}",
                )
        except Exception as audit_err:
            _logger.error(
                "audit row dropped after successful spool delete %s: %s",
                address,
                audit_err,
            )
            # Side-channel: best-effort surface the gap without
            # masking the original failure if this also breaks.
            # ``sanitize_audit_error`` strips bound DBAPI args so the
            # log row cannot inadvertently embed credentials lifted
            # from a future credential-bearing writer.
            try:
                with self._engine.begin() as conn:
                    self._audit.write(
                        conn,
                        action=mk_action("mailing_list", "audit_dropped"),
                        domain=domain,
                        data=(f"{address} original_error={sanitize_audit_error(audit_err)}"),
                    )
            except Exception as side_err:
                _logger.error(
                    "audit_dropped side-channel also failed for %s: %s",
                    address,
                    side_err,
                )
            raise

    def list_all(self, *, domain: str | None = None) -> list[MailingList]:
        """List all mlmmj lists, optionally filtered by FQDN."""
        return self._adapter.list_all(domain=domain)

    def _write_owner_alias(
        self,
        conn: Connection,
        list_address: str,
        owners: list[EmailStr],
    ) -> None:
        """Insert the ``<localpart>-owner@<domain>`` alias row whose
        ``goto`` resolves to the list's owners.

        v0.10: mail to ``<list>-owner@<domain>`` is rewritten by postfix
        ``virtual_alias_maps`` (PA's existing alias mysql lookup) to the
        owner addresses, then delivered through each owner's domain
        transport. Postino owns the row; it's audited under
        ``mailing_list.owner_alias_sync`` rather than ``alias.create`` so
        operators inspecting the log can trace it back to list ops."""
        localpart, _, domain = list_address.partition("@")
        owner_addr = f"{localpart}-owner@{domain}"
        goto = ",".join(str(o) for o in owners)
        alias = self._md.tables["alias"]
        now = self._clock()
        conn.execute(
            alias.insert().values(
                address=owner_addr,
                goto=goto,
                domain=domain,
                active=1,
                created=now,
                modified=now,
            )
        )
        self._audit.write(
            conn,
            action=mk_action("mailing_list", "owner_alias_sync"),
            domain=domain,
            data=f"{owner_addr} -> {goto}",
        )

    def _delete_owner_alias(self, conn: Connection, list_address: str) -> None:
        localpart, _, domain = list_address.partition("@")
        owner_addr = f"{localpart}-owner@{domain}"
        alias = self._md.tables["alias"]
        conn.execute(alias.delete().where(alias.c.address == owner_addr))

    def _validate_no_collision(self, conn: Connection, address: str) -> None:
        mailbox = self._md.tables["mailbox"]
        alias = self._md.tables["alias"]
        routes = self._md.tables["routes"]
        localpart, _, domain = address.partition("@")
        owner_alias_addr = f"{localpart}-owner@{domain}"
        if (
            conn.execute(select(mailbox.c.username).where(mailbox.c.username == address)).fetchone()
            is not None
        ):
            raise AlreadyExistsError(f"mailbox row already exists for {address!r}")
        if (
            conn.execute(
                select(alias.c.address).where(alias.c.address.in_([address, owner_alias_addr]))
            ).fetchone()
            is not None
        ):
            raise AlreadyExistsError(
                f"alias row already exists for {address!r} or {owner_alias_addr!r}"
            )
        if (
            conn.execute(
                select(routes.c.list_address).where(routes.c.list_address == address)
            ).fetchone()
            is not None
        ):
            raise AlreadyExistsError(f"routes row already exists for list {address!r}")
        if self._adapter.exists(address=address):
            raise AlreadyExistsError(f"mailing list {address!r} already exists")
