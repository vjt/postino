import pytest

from postino_core.enums import (
    DomainTransport,
    IdentityBackend,
    MailboxStatus,
    PasswordScheme,
    QuotaUnit,
)


def test_mailbox_status_int_values() -> None:
    assert int(MailboxStatus.ACTIVE) == 1
    assert int(MailboxStatus.DISABLED) == 0


def test_password_scheme_string_values() -> None:
    assert PasswordScheme.MD5_CRYPT.value == "MD5-CRYPT"
    assert PasswordScheme.BCRYPT.value == "BLF-CRYPT"
    assert PasswordScheme.SHA512_CRYPT.value == "SHA512-CRYPT"


def test_quota_unit_members() -> None:
    assert {u.value for u in QuotaUnit} == {"B", "K", "M", "G", "T"}


def test_domain_transport_members() -> None:
    assert DomainTransport.VIRTUAL.value == "virtual"
    assert DomainTransport.LMTP.value == "lmtp"
    assert DomainTransport.RELAY.value == "relay"


def test_identity_backend_members() -> None:
    assert IdentityBackend.LOCAL.value == "local"
    assert IdentityBackend.NOAUTH.value == "noauth"
    assert IdentityBackend.HYBRID.value == "hybrid"
    assert {b.value for b in IdentityBackend} == {"local", "noauth", "hybrid"}


def test_unknown_enum_raises() -> None:
    with pytest.raises(ValueError):
        MailboxStatus(99)


def test_domain_transport_mlmmj_raw_value() -> None:
    assert DomainTransport("mlmmj") is DomainTransport.MLMMJ
    assert DomainTransport.MLMMJ.value == "mlmmj"


def test_identity_backend_has_hybrid() -> None:
    assert IdentityBackend("hybrid") is IdentityBackend.HYBRID
    assert IdentityBackend.HYBRID.value == "hybrid"
