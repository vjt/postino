"""End-to-end app boot test: real PostinoSettings, real DI, /healthz responds."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from litestar.testing import AsyncTestClient

from .conftest import PreparedTestDB

pytestmark = pytest.mark.integration


@pytest.fixture
def postino_toml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prepared_test_db: PreparedTestDB,
    app_paths: tuple[Path, Path],
) -> Path:
    monkeypatch.setenv("POSTINOD_ZITADEL_HMAC_SECRET", "boot-secret")
    mail_root, postcreation_hook = app_paths
    p = tmp_path / "postino.toml"
    url = prepared_test_db.engine.url
    host = url.host or "localhost"
    username = url.username or ""
    password = url.password or ""
    database = url.database or ""
    p.write_text(
        dedent(
            f"""
            identity_backend = "noauth"
            postfix_sql_dir = "{tmp_path}"
            virtual_mailbox_base = "{mail_root}"
            postcreation_hook = "{postcreation_hook}"
            postcreation_hook_timeout = 5.0
            vmail_uid = -1
            vmail_gid = -1
            default_password_scheme = "BLF-CRYPT"
            default_quota_bytes = 1073741824

            [postinod]
            listen = "127.0.0.1:8443"
            zitadel_issuer = "https://zitadel.test"
            scim_issuer = "https://idp.test"
            scim_audience = "postinod"
            """
        ).strip()
        + "\n"
    )
    # Stub the sql-virtual_mailbox_maps.cf that PostinoSettings.mailbox_creds() reads.
    # build_services() calls this to build the DB engine — must point at the test DB.
    sql_cf = f"hosts = {host}\nuser = {username}\npassword = {password}\ndbname = {database}\n"
    (tmp_path / "sql-virtual_mailbox_maps.cf").write_text(sql_cf)
    return p


async def test_build_app_boots_and_serves_healthz(
    postino_toml: Path,
) -> None:
    from postinod.app import build_app

    app = build_app(toml_path=postino_toml)
    async with AsyncTestClient(app=app) as c:
        r = await c.get("/healthz")
        assert r.status_code == 200
