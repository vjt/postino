from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from postino_core.config_gen.input import GenInput, RenderContext
from postino_core.config_gen.templates import render_all
from postino_core.enums import IdentityBackend


def _ctx(mlmmj_on: bool) -> RenderContext:
    return RenderContext(
        input=GenInput(
            db_url=SecretStr("mysql+pymysql://u:p@h/d"),
            identity_backend=IdentityBackend.LOCAL,
            mlmmj_spool_dir=Path("/var/spool/mlmmj") if mlmmj_on else None,
        ),
        db_user="u",
        db_password=SecretStr("p"),
        db_host="h",
        db_port=3306,
        db_name="d",
        schema_version="v0.13.0",
    )


def test_mlmmj_off_skips_master_cf_and_sql_routes() -> None:
    names = {r.rel_path.name for r in render_all(_ctx(mlmmj_on=False))}
    assert "master.cf" not in names
    assert "sql-routes.cf" not in names


def test_mlmmj_off_main_cf_uses_virtual_transport_not_transport_maps() -> None:
    out = next(r for r in render_all(_ctx(mlmmj_on=False)) if r.rel_path.name == "main.cf")
    assert "virtual_transport = lmtp:unix:private/dovecot-lmtp" in out.content
    assert "transport_maps" not in out.content
    assert "sql-routes.cf" not in out.content


def test_mlmmj_on_main_cf_keeps_transport_maps() -> None:
    out = next(r for r in render_all(_ctx(mlmmj_on=True)) if r.rel_path.name == "main.cf")
    assert "transport_maps" in out.content
    assert "sql-routes.cf" in out.content
    assert "virtual_transport =" not in out.content


def test_mlmmj_on_emits_master_cf_and_sql_routes() -> None:
    names = {r.rel_path.name for r in render_all(_ctx(mlmmj_on=True))}
    assert "master.cf" in names
    assert "sql-routes.cf" in names
