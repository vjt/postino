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
    """Postfix transport protocol for a virtual domain.

    Holds only the protocol — the LMTP nexthop (e.g. ``unix:private/
    dovecot-lmtp``) is configured on PostinoSettings, not baked into
    the enum, so a stack with a TCP-listening dovecot can pick a
    different destination without code change.

    v0.10 BREAKING: the ``MLMMJ`` member was removed. Mailing lists no
    longer use domain-level transport; routing is per-list via the
    ``routes`` table. The ``domain.transport`` column still drives
    non-list mail (mailboxes, aliases).
    """

    VIRTUAL = "virtual"
    LMTP = "lmtp"
    RELAY = "relay"


class IdentityBackend(StrEnum):
    """Selects which IdentityProvider postino uses at runtime.

    LOCAL — passwords stored in PA ``mailbox.password`` (this CLI provisions).
    NOAUTH — ``mailbox.password`` carries the ``{NOAUTH}`` sentinel; an
    external IdP authenticates via dovecot-side passdb (LDAP/OIDC), and
    this CLI refuses to provision or rotate passwords.
    HYBRID — per-row policy: rows with `{NOAUTH}` defer to the IdP
    passdb; rows with a hash auth against passdb-sql. The CLI and SCIM
    can transition rows between the two states (claim_local /
    release_to_noauth).
    """

    LOCAL = "local"
    NOAUTH = "noauth"
    HYBRID = "hybrid"
