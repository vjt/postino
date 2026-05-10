"""Integration conftest for postinod.

Reuses the project-wide `db` engine fixture (defined in tests/conftest.py)
which already replays tests/fixtures/postfixadmin.sql and TRUNCATEs every
table per test. This conftest layers on top:

* exposes `prepared_test_db` as a (engine, metadata) bundle so tests can
  reflect once and reuse, mirroring how production wiring will pass them
  to the router (Task 15).
* seeds an `example.org` domain with capacity for the Zitadel-driven
  mailboxes the integration tests create.
* `StubJwks` — in-process JWKS stub for integration tests (reused by
  Task 13's Aliases router tests too).
* `app_paths` — pytest-managed tmp_path for mail_root + postcreation_hook
  so build_app_for_test callers don't leak temp dirs.
* Shared JWT fixtures (`keypair`, `auth_header`, `client`) used by both
  test_scim_users.py and test_scim_aliases.py.
"""

from __future__ import annotations

import base64
import collections.abc
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from litestar import Litestar
from litestar.testing import AsyncTestClient
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

_ISSUER = "https://idp.test"
_AUDIENCE = "postinod"
_KID = "test-kid"


@dataclass(frozen=True)
class PreparedTestDB:
    engine: Engine
    metadata: MetaData


class StubJwks:
    """In-process JWKS stub for integration tests.

    Satisfies JwksLike; resolves kid lookups from a static dict of JWK
    objects passed at construction. KeyError surfaces to JwtVerifier → 401.
    """

    def __init__(self, keys: list[dict[str, object]]) -> None:
        self._by_kid: dict[str, dict[str, object]] = {str(k["kid"]): k for k in keys}

    async def get(self, kid: str) -> dict[str, object]:
        return self._by_kid[kid]


@pytest.fixture
def prepared_test_db(db: Engine) -> Iterator[PreparedTestDB]:
    """Engine + reflected metadata + seeded `example.org` domain.

    `db` (root conftest) yields a TRUNCATEd-per-test engine. We reflect
    once here and seed a single test domain with capacity 100.
    """
    md = MetaData()
    md.reflect(bind=db)
    domain = md.tables["domain"]
    with db.begin() as conn:
        conn.execute(
            domain.insert().values(
                domain="example.org",
                description="postinod integration tests",
                aliases=100,
                mailboxes=100,
                maxquota=0,
                quota=1073741824,
                transport="virtual",
                backupmx=0,
                active=1,
            )
        )
    yield PreparedTestDB(engine=db, metadata=md)


@pytest.fixture
def app_paths(tmp_path: Path) -> tuple[Path, Path]:
    """Pytest-managed mail_root and postcreation_hook for build_app_for_test.

    Returns (mail_root, postcreation_hook). pytest cleans up tmp_path
    automatically, avoiding the leaked tempfile.mkdtemp() / NamedTemporaryFile
    that the old build_app_for_test optional-args approach produced.
    """
    mail_root = tmp_path / "vmail"
    mail_root.mkdir()
    hook = tmp_path / "post-creation.sh"
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)
    return mail_root, hook


@pytest.fixture(scope="module")
def keypair() -> RSAPrivateKey:
    """RSA key pair for signing test JWTs. Module-scoped: generated once per module."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def auth_header(keypair: RSAPrivateKey) -> dict[str, str]:
    """Signed Bearer token header for SCIM integration tests."""
    pem = keypair.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    token: str = jwt.encode(
        {
            "iss": _ISSUER,
            "aud": _AUDIENCE,
            "sub": "scim-client",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        pem,  # type: ignore[arg-type]  # WHY: cryptography returns bytes from private_bytes; pyjwt accepts bytes | str but is typed as str
        algorithm="RS256",
        headers={"kid": _KID},
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def client(
    prepared_test_db: PreparedTestDB,
    keypair: RSAPrivateKey,
    app_paths: tuple[Path, Path],
) -> collections.abc.AsyncGenerator[AsyncTestClient[Litestar], None]:
    """Async test client wired against a real test DB and stub JWKS."""
    from postinod.app import build_app_for_test

    def _b64(i: int) -> str:
        b = i.to_bytes((i.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).decode().rstrip("=")

    pub_numbers = keypair.public_key().public_numbers()
    jwk: dict[str, object] = {
        "kty": "RSA",
        "kid": _KID,
        "use": "sig",
        "alg": "RS256",
        "n": _b64(pub_numbers.n),
        "e": _b64(pub_numbers.e),
    }

    mail_root, postcreation_hook = app_paths
    jwks = StubJwks([jwk])
    app = build_app_for_test(
        db_engine=prepared_test_db.engine,
        metadata=prepared_test_db.metadata,
        hmac_secret=b"unused",
        mail_root=mail_root,
        postcreation_hook=postcreation_hook,
        scim_issuer=_ISSUER,
        scim_audience=_AUDIENCE,
        jwks=jwks,
    )
    async with AsyncTestClient(app=app) as c:
        yield c
