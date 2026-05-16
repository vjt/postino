"""Tests for templates.py: render loop, --only/--skip filter, dovecot auth branching."""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from postino_core.config_gen.input import GenInput, RenderContext
from postino_core.config_gen.templates import (
    _REGISTRY,  # pyright: ignore[reportPrivateUsage]  # WHY: structural test of registry shape
    registry_names,
    render_all,
)
from postino_core.enums import IdentityBackend


def _ctx(*, backend: IdentityBackend = IdentityBackend.LOCAL) -> RenderContext:
    return RenderContext(
        input=GenInput(
            db_url=SecretStr("mysql://u:p@h/d"),
            identity_backend=backend,
        ),
        db_user="postfix",
        db_password=SecretStr("test_password"),
        db_host="127.0.0.1",
        db_port=3306,
        db_name="postfix",
        schema_version="v0.12.0",
    )


def test_registry_count_is_twelve() -> None:
    assert len(_REGISTRY) == 12


def test_registry_names_returns_frozenset() -> None:
    names = registry_names()
    assert isinstance(names, frozenset)
    assert "master_cf" in names
    assert "dovecot_lmtp" in names


def test_render_all_emits_twelve() -> None:
    results = render_all(_ctx())
    rel_paths = {r.rel_path for r in results}
    assert Path("master.cf") in rel_paths
    assert Path("conf.d/auth-sql.conf.ext") in rel_paths
    assert Path("sql-virtual_alias_alias_domain_maps.cf") in rel_paths
    assert Path("sql-virtual_mailbox_alias_domain_maps.cf") in rel_paths
    assert Path("sql-routes.cf") in rel_paths
    assert len(results) == 12


def test_render_all_only_filter() -> None:
    results = render_all(_ctx(), only=frozenset({"master_cf"}))
    assert [r.rel_path for r in results] == [Path("master.cf")]


def test_render_all_skip_filter() -> None:
    results = render_all(_ctx(), skip=frozenset({"master_cf", "main_cf"}))
    rel_paths = {r.rel_path for r in results}
    assert Path("master.cf") not in rel_paths
    assert Path("main.cf") not in rel_paths


def test_dovecot_auth_branches_per_identity_backend() -> None:
    local = next(
        r
        for r in render_all(_ctx(backend=IdentityBackend.LOCAL))
        if r.rel_path == Path("conf.d/auth-sql.conf.ext")
    )
    hybrid = next(
        r
        for r in render_all(_ctx(backend=IdentityBackend.HYBRID))
        if r.rel_path == Path("conf.d/auth-sql.conf.ext")
    )
    noauth = next(
        r
        for r in render_all(_ctx(backend=IdentityBackend.NOAUTH))
        if r.rel_path == Path("conf.d/auth-sql.conf.ext")
    )
    assert "result_success" not in local.content
    assert "result_success = return-ok" in hybrid.content
    assert "result_success = continue-ok" in noauth.content
