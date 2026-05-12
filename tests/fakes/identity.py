"""In-memory IdentityProvider for unit tests.

Lets `MailboxService` exercise its compensation paths without spinning
up a real DB-backed provider. The fake records calls so tests can
assert on `create_identity`/`set_password`/`delete_identity` ordering."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import SecretStr
from sqlalchemy.engine import Connection

from postino_core.enums import PasswordScheme
from postino_core.providers.base import SENTINEL_NOAUTH


@dataclass
class FakeIdentityCall:
    """One recorded call against `FakeIdentityProvider`."""

    op: str
    username: str
    name: str | None = None
    password: SecretStr | None = None
    scheme: PasswordScheme | None = None


def _empty_calls() -> list[FakeIdentityCall]:
    return []


@dataclass
class FakeIdentityProvider:
    """Records ops; never touches the DB.

    `fail_on` lets a test trigger a failure path inside the mailbox tx —
    useful for `MailboxService.add` rollback coverage.
    """

    supports_password_change_value: bool = True
    supports_local_provisioning_value: bool = True
    supports_release_to_noauth_value: bool = True
    is_idp_managed_value: bool = False
    fail_on: str | None = None
    calls: list[FakeIdentityCall] = field(default_factory=_empty_calls)

    def create_identity(
        self,
        conn: Connection,
        username: str,
        name: str,
        password: SecretStr | None,
        scheme: PasswordScheme | None,
    ) -> None:
        self.calls.append(
            FakeIdentityCall(
                op="create",
                username=username,
                name=name,
                password=password,
                scheme=scheme,
            )
        )
        if self.fail_on == "create":
            raise RuntimeError("forced failure in create_identity")

    def set_password(
        self,
        conn: Connection,
        username: str,
        password: SecretStr,
        scheme: PasswordScheme,
    ) -> None:
        self.calls.append(
            FakeIdentityCall(
                op="set_password",
                username=username,
                password=password,
                scheme=scheme,
            )
        )

    def delete_identity(self, conn: Connection, username: str) -> None:
        self.calls.append(FakeIdentityCall(op="delete", username=username))

    def release_identity(self, conn: Connection, username: str) -> None:
        self.calls.append(FakeIdentityCall(op="release", username=username))

    def supports_password_change(self) -> bool:
        return self.supports_password_change_value

    def supports_local_provisioning(self) -> bool:
        return self.supports_local_provisioning_value

    def supports_release_to_noauth(self) -> bool:
        return self.supports_release_to_noauth_value

    def is_idp_managed(self, conn: Connection, username: str) -> bool:
        del conn, username
        return self.is_idp_managed_value

    def bootstrap_password_value(self) -> str:
        return SENTINEL_NOAUTH
