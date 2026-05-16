"""Generator tests:
- Collision: out_dir already has matching files, --in-place not set -> refuse
- Atomicity: render fails -> out_dir untouched, staging cleaned up
- Rollback: rename(staging, out_dir) fails in --in-place -> backup restored
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from postino_core.config_gen.generator import (
    _check_collision,  # pyright: ignore[reportPrivateUsage]  # WHY: unit test of internal helper
    generate,
)
from postino_core.config_gen.input import GenInput, RenderContext, RenderResult
from postino_core.enums import IdentityBackend
from postino_core.errors import CollisionRefused, RenderError


def _ctx() -> RenderContext:
    return RenderContext(
        input=GenInput(
            db_url=SecretStr("mysql://u:p@h/d"),
            identity_backend=IdentityBackend.LOCAL,
            skip_preflight=True,
            skip_postcheck=True,
        ),
        db_user="u",
        db_password=SecretStr("p"),
        db_host="h",
        db_port=3306,
        db_name="d",
        schema_version="v0.12.0",
    )


def test_check_collision_refuses_when_files_exist(tmp_path: Path) -> None:
    (tmp_path / "master.cf").write_text("existing\n")
    with pytest.raises(CollisionRefused):
        _check_collision(
            tmp_path,
            in_place=False,
            would_write=[Path("master.cf"), Path("main.cf")],
        )


def test_check_collision_allows_when_in_place(tmp_path: Path) -> None:
    (tmp_path / "master.cf").write_text("existing\n")
    _check_collision(tmp_path, in_place=True, would_write=[Path("master.cf")])


def test_render_failure_leaves_out_dir_untouched(tmp_path: Path) -> None:
    """If render_all raises, out_dir is not created and staging is cleaned up."""
    out_dir = tmp_path / "cfg"

    def boom(*args: object, **kwargs: object) -> list[RenderResult]:
        raise RenderError("master.cf.j2", RuntimeError("template KeyError"))

    with (
        patch("postino_core.config_gen.generator.render_all", side_effect=boom),
        patch(
            "postino_core.config_gen.generator._build_context",
            return_value=_ctx(),
        ),
        pytest.raises(RenderError),
    ):
        generate(_ctx().input, out_dir)
    assert not out_dir.exists() or list(out_dir.iterdir()) == []
    assert not (tmp_path / ".cfg.postino-gen.tmp").exists()


def test_rename_failure_restores_backup(tmp_path: Path) -> None:
    """Commit rename fails in --in-place -> out_dir restored from backup."""
    out_dir = tmp_path / "cfg"
    out_dir.mkdir()
    (out_dir / "preexisting.txt").write_text("original\n")

    real_rename = os.rename
    call_count = {"n": 0}

    def fake_rename(src: str, dst: str) -> None:
        call_count["n"] += 1
        # rename 1: out_dir -> backup (succeed)
        # rename 2: staging -> out_dir (raise — simulate failure)
        # rename 3: backup -> out_dir (succeed — rollback)
        if call_count["n"] == 2:
            raise OSError("simulated commit failure")
        real_rename(src, dst)

    fake_results = [RenderResult(rel_path=Path("master.cf"), content="ok\n", mode=0o644)]
    gi = _ctx().input.model_copy(update={"in_place": True})

    with (
        patch(
            "postino_core.config_gen.generator.render_all",
            return_value=fake_results,
        ),
        patch(
            "postino_core.config_gen.generator._build_context",
            return_value=_ctx(),
        ),
        patch(
            "postino_core.config_gen.generator.os.rename",
            side_effect=fake_rename,
        ),
        pytest.raises(OSError),
    ):
        generate(gi, out_dir)

    # out_dir restored — original file present
    assert (out_dir / "preexisting.txt").read_text() == "original\n"
    # backup + staging cleaned up
    assert not (tmp_path / "cfg.postino-gen.bak").exists()
    assert not (tmp_path / ".cfg.postino-gen.tmp").exists()
