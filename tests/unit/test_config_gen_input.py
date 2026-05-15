"""GenInput / RenderContext / RenderResult / GenResult validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from postino_core.config_gen.input import (
    GenInput,
    GenResult,
    RenderContext,
    RenderResult,
)
from postino_core.enums import IdentityBackend


def _valid_gi() -> GenInput:
    return GenInput(
        db_url=SecretStr("mysql+pymysql://u:p@h/d"),
        identity_backend=IdentityBackend.LOCAL,
    )


def test_gen_input_minimal_valid() -> None:
    gi = _valid_gi()
    assert gi.identity_backend == IdentityBackend.LOCAL
    assert gi.mlmmj_spool_dir == Path("/var/spool/mlmmj")


def test_gen_input_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        GenInput(
            db_url=SecretStr("mysql+pymysql://u:p@h/d"),
            identity_backend=IdentityBackend.LOCAL,
            bogus_key="x",  # type: ignore[call-arg]  # WHY: deliberate-extra-key test
        )


def test_gen_input_frozen() -> None:
    gi = _valid_gi()
    with pytest.raises(ValidationError):
        gi.in_place = True  # type: ignore[misc]  # WHY: deliberate-frozen-mutation test


def test_render_result_holds_rel_path_and_mode() -> None:
    rr = RenderResult(rel_path=Path("master.cf"), content="...", mode=0o644)
    assert rr.rel_path == Path("master.cf")
    assert rr.mode == 0o644


def test_render_context_password_is_secret() -> None:
    gi = _valid_gi()
    secret = "s3kr1t-CANARY-zzz"
    ctx = RenderContext(
        input=gi,
        db_user="u",
        db_password=SecretStr(secret),
        db_host="h",
        db_port=3306,
        db_name="d",
        has_alias_domains=True,
        has_routes_rows=True,
        schema_version="v0.12.0",
    )
    assert ctx.db_password.get_secret_value() == secret
    assert secret not in repr(ctx)  # SecretStr's repr redacts


def test_gen_result_is_frozen() -> None:
    gr = GenResult(written=[], preflight=[], postcheck=[], out_dir=Path("/tmp"))
    with pytest.raises(ValidationError):
        gr.out_dir = Path("/etc")  # type: ignore[misc]  # WHY: deliberate-frozen-mutation test
