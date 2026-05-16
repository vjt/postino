"""generate() -> re-parse emitted cfs -> no ERROR findings.

6 cases: identity_backend in {local, noauth, hybrid}
       x has_alias_domains in {False, True}
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text

from postino_core.config import parse_postfix_sql_cf
from postino_core.config_gen import GenInput, generate
from postino_core.enums import IdentityBackend

from ._schema_helpers import invoke_migrate


def _seed_alias_domain(db_url: str, populate: bool) -> None:
    engine = create_engine(db_url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM alias_domain"))
            if populate:
                conn.execute(
                    text(
                        "INSERT IGNORE INTO domain (domain, active) "
                        "VALUES ('example.org', '1'), ('alias.example.org', '1')"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO alias_domain "
                        "(alias_domain, target_domain, active) "
                        "VALUES ('alias.example.org', 'example.org', '1')"
                    )
                )
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    "backend",
    [IdentityBackend.LOCAL, IdentityBackend.NOAUTH, IdentityBackend.HYBRID],
)
@pytest.mark.parametrize("has_alias_domains", [False, True])
def test_roundtrip(
    db: Engine,
    tmp_path: Path,
    backend: IdentityBackend,
    has_alias_domains: bool,
) -> None:
    db_url = os.environ["POSTINO_TEST_DB_URL"]

    # `db` fixture truncated postino_schema_version; re-populate via the
    # canonical CLI path so preflight finds the required version row.
    code = invoke_migrate()
    assert code == 0, f"schema migrate exited {code}"

    _seed_alias_domain(db_url, populate=has_alias_domains)

    out_dir = tmp_path / "cfg"
    input_model = GenInput(
        db_url=SecretStr(db_url),
        identity_backend=backend,
    )
    result = generate(input_model, out_dir)

    # Core 3 sql cfs always emitted
    assert (out_dir / "sql-virtual_mailbox_maps.cf").exists()
    assert (out_dir / "sql-virtual_alias_maps.cf").exists()
    assert (out_dir / "sql-virtual_domains.cf").exists()

    # alias_domain pair: conditional
    if has_alias_domains:
        assert (out_dir / "sql-virtual_alias_alias_domain_maps.cf").exists()
        assert (out_dir / "sql-virtual_mailbox_alias_domain_maps.cf").exists()
    else:
        assert not (out_dir / "sql-virtual_alias_alias_domain_maps.cf").exists()
        assert not (out_dir / "sql-virtual_mailbox_alias_domain_maps.cf").exists()

    # Dovecot trio always emitted
    assert (out_dir / "dovecot-sql.conf.ext").exists()
    assert (out_dir / "conf.d" / "auth-sql.conf.ext").exists()
    assert (out_dir / "conf.d" / "20-lmtp.conf").exists()

    # Every sql cf parses cleanly with non-empty fields
    for p in out_dir.glob("sql-*.cf"):
        cfg = parse_postfix_sql_cf(p)
        assert cfg.host, p
        assert cfg.user, p
        assert cfg.password.get_secret_value(), p
        assert cfg.dbname, p

    # master.cf has all 4 canonical mlmmj flags
    master = (out_dir / "master.cf").read_text()
    assert "mlmmj-receive" in master and "-F" in master and "-e ${extension}" in master
    assert "mlmmj-bounce" in master and "-a ${sender}" in master
    assert "-a ${recipient}" not in master
    assert "mlmmj-sub" in master and "-m ${extension}" in master
    assert "mlmmj-unsub" in master and "-m ${extension}" in master

    # Identity-backend branch in auth-sql.conf.ext
    auth = (out_dir / "conf.d" / "auth-sql.conf.ext").read_text()
    if backend == IdentityBackend.LOCAL:
        assert "result_success" not in auth
    elif backend == IdentityBackend.HYBRID:
        assert "result_success = return-ok" in auth
    elif backend == IdentityBackend.NOAUTH:
        assert "result_success = continue-ok" in auth

    # Post-emit findings: no errors
    errors = [f for f in result.postcheck if f.severity == "error"]
    assert not errors, errors
