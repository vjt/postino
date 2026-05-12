"""Conformance: every IdentityProvider honours the Protocol's contract.

Parametrised across the two shipped providers (Local + NoAuth). The
contracts asserted here are what `MailboxService` relies on:

* providers participate in the caller's transaction — they do not
  commit on their own,
* `delete_identity` is idempotent,
* `set_password` rowcount semantics match the provider's promise
  (Local raises NotFoundError on missing rows; NoAuth refuses
  unconditionally),
* `create_identity` either rewrites `mailbox.password` (Local) or
  leaves the `{NOAUTH}` sentinel in place (NoAuth).

Per-provider unit tests live in `test_local_provider.py` /
`test_noauth_provider.py`; this file is the cross-provider seam.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import pytest
from pydantic import SecretStr
from sqlalchemy import MetaData, select
from sqlalchemy.engine import Connection, Engine

from postino_core.enums import PasswordScheme
from postino_core.errors import ConfigError, MailctlError, NotFoundError
from postino_core.password import verify_password
from postino_core.providers import (
    SENTINEL_NOAUTH,
    IdentityProvider,
    LocalProvider,
    NoAuthProvider,
)

pytestmark = pytest.mark.integration


def _build_local(md: MetaData) -> IdentityProvider:
    return LocalProvider(metadata=md, clock=lambda: datetime(2026, 5, 9, 12, 0, 0))


def _build_noauth(md: MetaData) -> IdentityProvider:
    return NoAuthProvider(metadata=md)


_BUILDERS: list[tuple[str, Callable[[MetaData], IdentityProvider]]] = [
    ("local", _build_local),
    ("noauth", _build_noauth),
]


@pytest.fixture(params=_BUILDERS, ids=lambda p: p[0])
def provider(
    request: pytest.FixtureRequest,
    db: Engine,
) -> tuple[str, IdentityProvider, MetaData]:
    name, build = request.param  # type: ignore[misc]  # WHY: pytest's request.param is typed as Any.
    md = MetaData()
    md.reflect(bind=db)
    builder: Callable[[MetaData], IdentityProvider] = build
    backend_name: str = name
    return backend_name, builder(md), md


def _seed_mailbox(conn: Connection, md: MetaData, username: str) -> None:
    """Insert a fresh mailbox row with the `{NOAUTH}` sentinel password.

    Mirrors what MailboxService.add does up to the sentinel write — the
    point in the lifecycle where IdentityProvider.create_identity is
    expected to run.
    """
    domain = md.tables["domain"]
    mailbox = md.tables["mailbox"]
    if not conn.execute(select(domain).where(domain.c.domain == "example.com")).fetchone():
        conn.execute(
            domain.insert().values(
                domain="example.com",
                description="",
                aliases=0,
                mailboxes=0,
                maxquota=0,
                quota=0,
                transport="virtual",
                backupmx=0,
                active=1,
            )
        )
    local_part = username.split("@", 1)[0]
    conn.execute(
        mailbox.insert().values(
            username=username,
            password=SENTINEL_NOAUTH,
            name="conformance",
            maildir=f"example.com/{local_part}/",
            quota=0,
            local_part=local_part,
            domain="example.com",
            active=1,
        )
    )


def _read_password(conn: Connection, md: MetaData, username: str) -> str:
    """Return the current `mailbox.password` value (caller asserts on it)."""
    row = conn.execute(
        select(md.tables["mailbox"].c.password).where(md.tables["mailbox"].c.username == username)
    ).scalar_one()
    return str(row)


def test_supports_predicates_are_bool(
    provider: tuple[str, IdentityProvider, MetaData],
) -> None:
    _, p, _ = provider
    assert isinstance(p.supports_password_change(), bool)
    assert isinstance(p.supports_local_provisioning(), bool)


def test_create_identity_round_trip(
    provider: tuple[str, IdentityProvider, MetaData],
    db: Engine,
) -> None:
    """Local rewrites mailbox.password to a verifying hash. NoAuth leaves the sentinel."""
    backend, p, md = provider
    with db.begin() as conn:
        _seed_mailbox(conn, md, "rt@example.com")
        p.create_identity(
            conn,
            "rt@example.com",
            name="rt",
            password=SecretStr("hunter2") if backend == "local" else None,
            scheme=PasswordScheme.BCRYPT if backend == "local" else None,
        )
        stored = _read_password(conn, md, "rt@example.com")
    if backend == "local":
        assert stored != SENTINEL_NOAUTH
        assert verify_password(SecretStr("hunter2"), stored) is True
    else:
        assert stored == SENTINEL_NOAUTH


def test_set_password_rowcount_semantics(
    provider: tuple[str, IdentityProvider, MetaData],
    db: Engine,
) -> None:
    """Local raises NotFoundError for missing rows; NoAuth raises ConfigError unconditionally."""
    backend, p, _md = provider
    with db.begin() as conn:
        try:
            p.set_password(
                conn,
                "ghost@example.com",
                SecretStr("x"),
                PasswordScheme.BCRYPT,
            )
        except MailctlError as e:
            if backend == "local":
                assert isinstance(e, NotFoundError)
            else:
                assert isinstance(e, ConfigError)
        else:
            pytest.fail(f"{backend}: set_password on missing row did not raise")


def test_delete_identity_is_idempotent(
    provider: tuple[str, IdentityProvider, MetaData],
    db: Engine,
) -> None:
    """Both providers accept a second delete of an already-absent identity."""
    _, p, _ = provider
    with db.begin() as conn:
        p.delete_identity(conn, "ghost@example.com")
        p.delete_identity(conn, "ghost@example.com")  # second call must be a no-op


def test_provider_mutation_visible_only_on_commit(
    provider: tuple[str, IdentityProvider, MetaData],
    db: Engine,
) -> None:
    """Provider writes participate in the caller's transaction.

    Roll back the outer transaction and assert the mutation vanishes — the
    direct read on a fresh connection must see the original sentinel.

    For NoAuth this is a positive assertion in disguise: even though
    NoAuth.create_identity is a no-op (and rejects a non-None password),
    we still run it inside a rolled-back tx with ``password=None`` and
    confirm the sentinel is intact afterwards. That conformance check
    locks the "NoAuth never mutates mailbox.password" contract into the
    shared matrix instead of carrying it as a comment elsewhere.
    """
    backend, p, md = provider

    # Stage 1: seed and commit so the row exists outside the rollback scope.
    with db.begin() as conn:
        _seed_mailbox(conn, md, "rb@example.com")

    # Stage 2: mutate inside a transaction, then explicitly rollback.
    # Under noauth pass password=None/scheme=None (NoAuth rejects non-None);
    # the no-op call must still leave the sentinel intact after rollback.
    password: SecretStr | None = SecretStr("rolledback") if backend == "local" else None
    scheme: PasswordScheme | None = PasswordScheme.BCRYPT if backend == "local" else None
    raw = db.connect()
    try:
        tx = raw.begin()
        p.create_identity(
            raw,
            "rb@example.com",
            name="rb",
            password=password,
            scheme=scheme,
        )
        tx.rollback()
    finally:
        raw.close()

    # Stage 3: fresh connection sees the original sentinel.
    with db.connect() as conn:
        assert _read_password(conn, md, "rb@example.com") == SENTINEL_NOAUTH


def test_provider_does_not_call_commit_itself(
    provider: tuple[str, IdentityProvider, MetaData],
    db: Engine,
) -> None:
    """A provider that called `conn.commit()` would end the caller's tx.

    Track commit calls via a counter wrapped around the real method; after
    create_identity / set_password / delete_identity, count must still be 0.
    """
    backend, p, md = provider
    with db.begin() as conn:
        _seed_mailbox(conn, md, "nc@example.com")

    raw = db.connect()
    try:
        tx = raw.begin()
        calls = {"commit": 0}
        original_commit = raw.commit

        def _counting_commit() -> None:
            calls["commit"] += 1
            original_commit()

        raw.commit = _counting_commit  # type: ignore[method-assign]  # WHY: test-only swap; reverted via close below
        if backend == "local":
            p.create_identity(
                raw,
                "nc@example.com",
                name="nc",
                password=SecretStr("x"),
                scheme=PasswordScheme.BCRYPT,
            )
            p.set_password(raw, "nc@example.com", SecretStr("y"), PasswordScheme.BCRYPT)
        else:
            p.create_identity(raw, "nc@example.com", name="nc", password=None, scheme=None)
        p.delete_identity(raw, "nc@example.com")
        tx.rollback()
    finally:
        raw.close()
    assert calls["commit"] == 0
