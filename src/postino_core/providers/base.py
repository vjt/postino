"""IdentityProvider Protocol — the seam between local and external identity backends.

Two implementations ship in this build:

* ``LocalProvider`` — passwords stored in PA ``mailbox.password``. The
  CLI provisions them. ``supports_password_change`` and
  ``supports_local_provisioning`` both return True.
* ``NoAuthProvider`` — ``mailbox.password`` is left as ``{NOAUTH}``. An
  external IdP authenticates dovecot via passdb. The CLI cannot
  provision or rotate passwords; both ``supports_*`` predicates return
  False.

Boundary types: ``username`` is a plain ``str`` here. The
``EmailStr`` validation lives at the CLI / model boundary
(``MailboxCreate.username: EmailStr``); providers receive an already-
validated string and do not re-validate.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import SecretStr
from sqlalchemy.engine import Connection

from postino_core.enums import PasswordScheme

# Sentinel value written to PA `mailbox.password` whenever postino
# cannot fill the column with a real hash — either as the bootstrap
# placeholder before LocalProvider replaces it, or as the permanent
# value under NoAuthProvider. Dovecot's passdb-sql query treats
# `{NOAUTH}` as "no local credential; defer to other passdbs".
SENTINEL_NOAUTH = "{NOAUTH}"


class IdentityProvider(Protocol):
    """Owner of authentication identity for a mailbox."""

    def create_identity(
        self,
        conn: Connection,
        username: str,
        name: str,
        password: SecretStr | None,
        scheme: PasswordScheme | None,
    ) -> None:
        """Establish the identity. Called immediately after the mailbox
        row INSERT (with the ``{NOAUTH}`` sentinel password).

        ``LocalProvider`` UPDATEs the password column with the hash
        derived from ``password``/``scheme``, and raises ``ConfigError``
        if either is None. ``NoAuthProvider`` is a no-op (the sentinel
        already in place is the permanent value)."""
        ...

    def set_password(
        self,
        conn: Connection,
        username: str,
        password: SecretStr,
        scheme: PasswordScheme,
    ) -> None:
        """Change the password for an existing identity.

        Returns None on success.
        Raises NotFoundError if identity is absent; ConfigError if scheme
        is unsupported."""
        ...

    def delete_identity(
        self,
        conn: Connection,
        username: str,
    ) -> None:
        """Remove identity (idempotent — no error if absent).

        For LocalProvider this is a no-op (the mailbox row deletion in
        MailboxService.delete already drops the row's password)."""
        ...

    def release_identity(
        self,
        conn: Connection,
        username: str,
    ) -> None:
        """Reset the row to the ``{NOAUTH}`` sentinel (release to IdP).

        Inverse of ``set_password`` for hybrid deployments. Idempotent: a
        row already on the sentinel returns without writing. Raises
        ``ConfigError`` under backends that do not own a credential column
        (NoAuthProvider — sentinel is always the value) or backends that
        refuse credential lifecycle transitions (LocalProvider — the
        deployment-wide contract is that every row carries a hash)."""
        ...

    def supports_release_to_noauth(self) -> bool:
        """Whether ``release_identity`` can transition rows back to sentinel.

        Distinct from ``supports_password_change``: LocalProvider supports
        the latter (rotate within SQL auth) but not the former (cannot
        release rows to IdP under a no-IdP deployment). Only HybridProvider
        returns True."""
        ...

    def supports_password_change(self) -> bool:
        """Whether ``postino user passwd`` is exposed."""
        ...

    def supports_local_provisioning(self) -> bool:
        """Whether ``postino user add`` accepts a password.

        Returns False for backends where the IdP owns user lifecycle —
        in that mode users must be created in the IdP first and the
        mailbox row gets the ``{NOAUTH}`` sentinel."""
        ...

    def is_idp_managed(self, conn: Connection, username: str) -> bool:
        """Return True if ``username``'s credential is currently owned by
        the external IdP (i.e. the row carries the ``{NOAUTH}`` sentinel).

        Exists so ``MailboxService`` doesn't have to read the password
        column directly — the credential-format awareness stays inside
        the provider. ``LocalProvider`` always returns False;
        ``NoAuthProvider`` always returns True; ``HybridProvider`` reads
        the column.

        Raises ``NotFoundError`` if the mailbox row does not exist."""
        ...

    def bootstrap_password_value(self) -> str:
        """The literal value the provider wants to see in
        ``mailbox.password`` right after the bootstrap INSERT — before
        ``create_identity`` may rewrite it.

        Returning the ``{NOAUTH}`` sentinel is the default; backends that
        want a different placeholder (e.g. an empty string under a
        future LDAP-resident scheme) override here without forcing
        ``MailboxService`` to know."""
        ...
