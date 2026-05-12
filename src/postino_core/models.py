"""Pydantic models — boundary types for postino.

All models are frozen + strict. Fields use the most specific type
available (EmailStr, Path, datetime). No coercion.

Optional[...] is used only when a column is genuinely nullable in
the PostfixAdmin schema. Convention: missing string fields use ""
explicitly (the caller decides), not Optional[str]."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, EmailStr, SecretStr, field_validator, model_validator

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

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

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
    """Inputs for `postino user add`. Built at the CLI boundary.

    ``password`` and ``scheme`` are optional so the same payload covers
    both the LOCAL backend (which provisions ``mailbox.password`` from
    these fields) and the NOAUTH backend (where dovecot authenticates
    via an external IdP and the ``{NOAUTH}`` sentinel stays in place).
    A ``model_validator`` rejects the half-state — both must be set or
    both None — so providers don't have to re-validate at the mutator
    layer and a typo at the CLI boundary surfaces immediately.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    username: EmailStr
    password: SecretStr | None = None
    name: str
    quota_bytes: int
    scheme: PasswordScheme | None = None

    @model_validator(mode="after")
    def _password_scheme_pair(self) -> MailboxCreate:
        if (self.password is None) != (self.scheme is None):
            raise ValueError(
                "password and scheme must be supplied together (both set or both None)"
            )
        return self


class MailboxUsage(BaseModel):
    """Live usage row from the quota2 table."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    username: EmailStr
    bytes_used: int
    messages: int


class Alias(BaseModel):
    """An alias row from the PostfixAdmin alias table."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    address: EmailStr
    goto: str
    domain: str
    status: MailboxStatus
    created: datetime
    modified: datetime


class Domain(BaseModel):
    """A domain row from the PostfixAdmin domain table."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

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


class AliasDomain(BaseModel):
    """An alias_domain row from the PostfixAdmin alias_domain table.

    Maps domain A → domain B so that mail to user@A is delivered as
    user@B by postfix's virtual_alias_domain_maps."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    alias_domain: str
    target_domain: str
    status: MailboxStatus
    created: datetime
    modified: datetime


class MailingListCreate(BaseModel):
    """Inputs for `postino list add`. Multi-owner: the first owner is
    handed to ``mlmmj-make-ml -o``; the rest are appended to the spool's
    ``control/owner`` file under flock."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    address: EmailStr
    owners: list[EmailStr]

    @field_validator("owners")
    @classmethod
    def _at_least_one(cls, v: list[EmailStr]) -> list[EmailStr]:
        if not v:
            raise ValueError("at least one owner required")
        return v


class MailingList(BaseModel):
    """A mlmmj mailing list as observed on the filesystem.

    ``spool_dir`` is the absolute on-disk path under
    ``mlmmj_spool_dir / address``; useful for ops debugging via
    ``postino list show``."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    address: EmailStr
    owners: list[EmailStr]
    subscriber_count: int
    spool_dir: Path
