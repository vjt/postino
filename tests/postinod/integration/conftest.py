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
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine


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
