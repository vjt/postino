"""End-to-end CLI tests for `postino list`.

Spawns real subprocesses (`python -m postino list ...`) against a real
DB + real mlmmj binaries + tmp spool. Skipped when mlmmj-sub is not
installed (v0.5+ writes the spool directly; only the subscriber-mgmt
binaries are still shelled out)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e_cli.conftest import WriteEnv

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("mlmmj-sub") is None,
        reason=(
            "mlmmj-sub not on PATH; install mlmmj to run this suite. "
            "v0.5+ writes the spool layout directly; only mlmmj-sub/unsub/list "
            "are required at runtime."
        ),
    ),
]


def _run_postino(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "postino", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture
def list_env(e2e_write_env: WriteEnv, tmp_path: Path) -> WriteEnv:
    """Variant of e2e_write_env that adds mlmmj spool config + mlmmj domain."""
    spool = tmp_path / "spool"
    spool.mkdir()
    env = dict(e2e_write_env.env)
    env["POSTINO_MLMMJ_SPOOL_DIR"] = str(spool)
    env["POSTINO_MLMMJ_UID"] = "-1"
    env["POSTINO_MLMMJ_GID"] = "-1"

    # Seed the lists.example.org domain (virtual transport; v0.10 no longer uses mlmmj transport).
    md = e2e_write_env.metadata
    with e2e_write_env.engine.begin() as conn:
        conn.execute(
            md.tables["domain"]
            .insert()
            .values(
                domain="lists.example.org",
                description="e2e mlmmj domain",
                aliases=0,
                mailboxes=0,
                maxquota=0,
                quota=0,
                transport="virtual",
                backupmx=0,
                created="2026-05-09 12:00:00",
                modified="2026-05-09 12:00:00",
                active=1,
            )
        )
    return WriteEnv(
        env=env,
        mail_root=e2e_write_env.mail_root,
        engine=e2e_write_env.engine,
        metadata=e2e_write_env.metadata,
    )


def test_list_add_creates_spool_tree(list_env: WriteEnv) -> None:
    spool = Path(list_env.env["POSTINO_MLMMJ_SPOOL_DIR"])
    r = _run_postino(
        list_env.env,
        "list",
        "add",
        "team@lists.example.org",
        "--owner",
        "alice@example.org",
    )
    assert r.returncode == 0, r.stderr
    assert (spool / "team@lists.example.org" / "control" / "owner").exists()


def test_list_sub_unsub_round_trip(list_env: WriteEnv) -> None:
    _run_postino(
        list_env.env,
        "list",
        "add",
        "team@lists.example.org",
        "--owner",
        "alice@example.org",
    )
    r = _run_postino(
        list_env.env,
        "list",
        "sub",
        "team@lists.example.org",
        "bob@example.org",
    )
    assert r.returncode == 0, r.stderr
    r = _run_postino(list_env.env, "--json", "list", "show", "team@lists.example.org")
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["subscriber_count"] == 1

    r = _run_postino(
        list_env.env,
        "list",
        "unsub",
        "team@lists.example.org",
        "bob@example.org",
    )
    assert r.returncode == 0
    r = _run_postino(list_env.env, "--json", "list", "show", "team@lists.example.org")
    payload = json.loads(r.stdout)
    assert payload["subscriber_count"] == 0


def test_list_rm_refuses_non_empty(list_env: WriteEnv) -> None:
    _run_postino(
        list_env.env,
        "list",
        "add",
        "team@lists.example.org",
        "--owner",
        "alice@example.org",
    )
    _run_postino(
        list_env.env,
        "list",
        "sub",
        "team@lists.example.org",
        "bob@example.org",
    )
    r = _run_postino(list_env.env, "list", "rm", "team@lists.example.org", "--yes")
    assert r.returncode == 3  # CapacityError


def test_list_rm_force_removes_spool(list_env: WriteEnv) -> None:
    spool = Path(list_env.env["POSTINO_MLMMJ_SPOOL_DIR"])
    _run_postino(
        list_env.env,
        "list",
        "add",
        "team@lists.example.org",
        "--owner",
        "alice@example.org",
    )
    _run_postino(
        list_env.env,
        "list",
        "sub",
        "team@lists.example.org",
        "bob@example.org",
    )
    r = _run_postino(
        list_env.env,
        "list",
        "rm",
        "team@lists.example.org",
        "--yes",
        "--force",
    )
    assert r.returncode == 0
    assert not (spool / "team@lists.example.org").exists()


def test_list_unconfigured_exits_4(e2e_write_env: WriteEnv) -> None:
    """Without POSTINO_MLMMJ_SPOOL_DIR every list subcommand must exit 4."""
    env = dict(e2e_write_env.env)
    env.pop("POSTINO_MLMMJ_SPOOL_DIR", None)
    r = _run_postino(
        env,
        "list",
        "add",
        "team@lists.example.org",
        "--owner",
        "alice@example.org",
    )
    assert r.returncode == 4
    assert "POSTINO_MLMMJ_SPOOL_DIR" in r.stderr
