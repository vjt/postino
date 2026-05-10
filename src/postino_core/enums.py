"""Enumerations used across postino_core."""

from __future__ import annotations

from enum import IntEnum, StrEnum


class MailboxStatus(IntEnum):
    """Maps to PostfixAdmin mailbox.active / alias.active / domain.active."""

    ACTIVE = 1
    DISABLED = 0


class PasswordScheme(StrEnum):
    """Dovecot pass_scheme tags. The value is the prefix dovecot expects
    in {scheme}hash form. Existing m42 rows are MD5-CRYPT (legacy
    PostfixAdmin default). New mailboxes default to BLF-CRYPT (bcrypt)."""

    MD5_CRYPT = "MD5-CRYPT"
    BCRYPT = "BLF-CRYPT"
    SHA512_CRYPT = "SHA512-CRYPT"


class QuotaUnit(StrEnum):
    """Suffix → binary multiplier (1 K = 1024). Parsed by quota.parse_quota."""

    B = "B"
    K = "K"
    M = "M"
    G = "G"
    T = "T"


class DomainTransport(StrEnum):
    """Postfix transport for a virtual domain."""

    VIRTUAL = "virtual"
    LMTP = "lmtp:unix:private/dovecot-lmtp"
    RELAY = "relay"


class IdentityBackend(StrEnum):
    """Selects which IdentityProvider postino uses at runtime.

    LOCAL — passwords stored in PA ``mailbox.password`` (this CLI provisions).
    NOAUTH — ``mailbox.password`` carries the ``{NOAUTH}`` sentinel; an
    external IdP authenticates via dovecot-side passdb (LDAP/OIDC), and
    this CLI refuses to provision or rotate passwords.
    """

    LOCAL = "local"
    NOAUTH = "noauth"
