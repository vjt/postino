from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError
from pydantic import ValidationError as PydValidationError

from postino_core.enums import (
    DomainTransport,
    MailboxStatus,
    PasswordScheme,
)
from postino_core.models import (
    Alias,
    AliasDomain,
    Domain,
    Mailbox,
    MailboxCreate,
    MailboxUsage,
    MailingList,
    MailingListCreate,
)


def _now() -> datetime:
    return datetime(2026, 5, 9, 12, 0, 0)


def test_mailbox_round_trip() -> None:
    m = Mailbox(
        username="foo@example.com",
        name="Foo Bar",
        maildir=Path("example.com/foo/"),
        quota_bytes=5 * 1024**3,
        local_part="foo",
        domain="example.com",
        status=MailboxStatus.ACTIVE,
        created=_now(),
        modified=_now(),
    )
    assert m.username == "foo@example.com"
    assert m.quota_bytes == 5 * 1024**3
    assert m.status == MailboxStatus.ACTIVE


def test_mailbox_is_frozen() -> None:
    m = Mailbox(
        username="foo@example.com",
        name="",
        maildir=Path("example.com/foo/"),
        quota_bytes=0,
        local_part="foo",
        domain="example.com",
        status=MailboxStatus.ACTIVE,
        created=_now(),
        modified=_now(),
    )
    with pytest.raises(ValidationError):
        m.username = "other@example.com"  # type: ignore[misc]


def test_mailbox_invalid_email_rejected() -> None:
    with pytest.raises(ValidationError):
        Mailbox(
            username="not-an-email",
            name="",
            maildir=Path("x/"),
            quota_bytes=0,
            local_part="x",
            domain="x",
            status=MailboxStatus.ACTIVE,
            created=_now(),
            modified=_now(),
        )


def test_mailbox_strict_no_coercion() -> None:
    """quota_bytes is int — strict mode rejects '5'."""
    with pytest.raises(ValidationError):
        Mailbox(
            username="foo@example.com",
            name="",
            maildir=Path("x/"),
            quota_bytes="5",  # type: ignore[arg-type]
            local_part="foo",
            domain="example.com",
            status=MailboxStatus.ACTIVE,
            created=_now(),
            modified=_now(),
        )


def test_mailbox_create_carries_secret() -> None:
    c = MailboxCreate(
        username="foo@example.com",
        password=SecretStr("hunter2"),
        name="",
        quota_bytes=0,
        scheme=PasswordScheme.BCRYPT,
    )
    assert c.password is not None
    assert c.password.get_secret_value() == "hunter2"
    assert "hunter2" not in repr(c)


def test_mailbox_create_password_and_scheme_default_to_none() -> None:
    """NOAUTH backends pass no password/scheme; defaults make that legal."""
    c = MailboxCreate(
        username="foo@example.com",
        name="",
        quota_bytes=0,
    )
    assert c.password is None
    assert c.scheme is None


def test_alias_required_fields() -> None:
    a = Alias(
        address="foo@example.com",
        goto="bar@example.com",
        domain="example.com",
        status=MailboxStatus.ACTIVE,
        created=_now(),
        modified=_now(),
    )
    assert a.address == "foo@example.com"


def test_domain_required_fields() -> None:
    d = Domain(
        domain="example.com",
        description="example",
        max_aliases=0,
        max_mailboxes=0,
        max_quota_bytes=0,
        default_quota_bytes=1024**3,
        transport=DomainTransport.LMTP,
        backupmx=False,
        status=MailboxStatus.ACTIVE,
        created=_now(),
        modified=_now(),
    )
    assert d.transport == DomainTransport.LMTP


def test_mailbox_usage_basic() -> None:
    u = MailboxUsage(
        username="foo@example.com",
        bytes_used=1024,
        messages=3,
    )
    assert u.bytes_used == 1024


def test_mailing_list_create_requires_at_least_one_owner() -> None:
    with pytest.raises(ValidationError):
        MailingListCreate(address="team@lists.example.org", owners=[])


def test_mailing_list_create_accepts_multiple_owners() -> None:
    m = MailingListCreate(
        address="team@lists.example.org",
        owners=["alice@example.org", "bob@example.org"],
    )
    assert len(m.owners) == 2
    assert str(m.address) == "team@lists.example.org"


def test_mailing_list_is_frozen() -> None:
    m = MailingList(
        address="team@lists.example.org",
        owners=["alice@example.org"],
        subscriber_count=0,
        spool_dir=Path("/var/spool/mlmmj/team@lists.example.org"),
    )
    with pytest.raises(ValidationError):
        m.subscriber_count = 5  # type: ignore[misc]  # WHY: testing frozen-model rejection


def test_mailing_list_create_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        MailingListCreate(
            address="team@lists.example.org",
            owners=["alice@example.org"],
            subscriber_count=0,  # type: ignore[call-arg]  # WHY: testing extra="forbid"
        )


def _ts() -> datetime:
    return datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)


def test_alias_domain_model_constructs() -> None:
    row = AliasDomain(
        alias_domain="aliasdom.it",
        target_domain="target.com",
        status=MailboxStatus.ACTIVE,
        created=_ts(),
        modified=_ts(),
    )
    assert row.alias_domain == "aliasdom.it"
    assert row.target_domain == "target.com"
    assert row.status is MailboxStatus.ACTIVE


def test_alias_domain_model_is_frozen() -> None:
    row = AliasDomain(
        alias_domain="aliasdom.it",
        target_domain="target.com",
        status=MailboxStatus.ACTIVE,
        created=_ts(),
        modified=_ts(),
    )
    with pytest.raises(PydValidationError):
        row.alias_domain = "other.com"  # type: ignore[misc]  # WHY: frozen model rejects mutation; test asserts that.


def test_alias_domain_model_rejects_extras() -> None:
    with pytest.raises(PydValidationError):
        AliasDomain.model_validate({
            "alias_domain": "x.it",
            "target_domain": "y.it",
            "status": 1,
            "created": _ts(),
            "modified": _ts(),
            "stray": "field",
        })
