"""Pydantic models — boundary types for postino.

All models are frozen + strict. Fields use the most specific type
available (EmailStr, Path, datetime). No coercion.

Optional[...] is used only when a column is genuinely nullable in
the PostfixAdmin schema. Convention: missing string fields use ""
explicitly (the caller decides), not Optional[str]."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, EmailStr, SecretStr

from postino_core.enums import (
    DomainTransport,
    MailboxStatus,
    PasswordScheme,
)


class Mailbox(BaseModel):
    """A parsed mailbox row from the PostfixAdmin schema.

    Returns: a validated mailbox.
    Raises: pydantic.ValidationError on schema mismatch.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    username: EmailStr
    name: str
    maildir: Path
    quota_bytes: int
    local_part: str
    domain: str
    status: MailboxStatus
    created: datetime
    modified: datetime


class MailboxCreate(BaseModel):
    """Inputs for `postino user add`. Built at the CLI boundary."""

    model_config = ConfigDict(frozen=True, strict=True)

    username: EmailStr
    password: SecretStr
    name: str
    quota_bytes: int
    scheme: PasswordScheme


class MailboxUsage(BaseModel):
    """Live usage row from the quota2 table."""

    model_config = ConfigDict(frozen=True, strict=True)

    username: EmailStr
    bytes_used: int
    messages: int


class Alias(BaseModel):
    """An alias row from the PostfixAdmin alias table."""

    model_config = ConfigDict(frozen=True, strict=True)

    address: EmailStr
    goto: str
    domain: str
    status: MailboxStatus
    created: datetime
    modified: datetime


class Domain(BaseModel):
    """A domain row from the PostfixAdmin domain table."""

    model_config = ConfigDict(frozen=True, strict=True)

    domain: str
    description: str
    max_aliases: int
    max_mailboxes: int
    max_quota_bytes: int
    default_quota_bytes: int
    transport: DomainTransport
    backupmx: bool
    status: MailboxStatus
    created: datetime
    modified: datetime
