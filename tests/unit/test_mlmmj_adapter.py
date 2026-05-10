"""Unit tests for MlmmjAdapter. Pure subprocess.run mocking — no real
mlmmj binaries needed; CI installs the real binary and the integration
suite exercises it."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from postino_core.adapters.mlmmj import MlmmjAdapter
from postino_core.errors import AlreadyExistsError, MlmmjError, NotFoundError


def _adapter(tmp_path: Path) -> MlmmjAdapter:
    return MlmmjAdapter(
        spool_root=tmp_path,
        mlmmj_uid=-1,
        mlmmj_gid=-1,
        timeout=5.0,
    )


def test_create_invokes_mlmmj_make_ml_with_correct_argv(tmp_path: Path) -> None:
    a = _adapter(tmp_path)
    with patch("postino_core.adapters.mlmmj.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        a.create(address="team@lists.example.org", primary_owner="alice@example.org")

    args, kwargs = run.call_args
    cmd = args[0]
    assert cmd[0] == "mlmmj-make-ml"
    assert "-L" in cmd
    assert str(tmp_path / "team@lists.example.org") in cmd
    assert "-a" in cmd
    assert "team@lists.example.org" in cmd
    assert "-h" in cmd
    assert "lists.example.org" in cmd
    assert "-o" in cmd
    assert "alice@example.org" in cmd
    assert "-s" in cmd  # silent; no interactive prompts
    assert kwargs["timeout"] == 5.0
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_create_raises_already_exists_on_existing_dir(tmp_path: Path) -> None:
    listdir = tmp_path / "team@lists.example.org"
    listdir.mkdir()
    a = _adapter(tmp_path)
    with (
        patch("postino_core.adapters.mlmmj.subprocess.run") as run,
        pytest.raises(AlreadyExistsError),
    ):
        a.create(address="team@lists.example.org", primary_owner="alice@example.org")
    run.assert_not_called()  # short-circuited before subprocess


def test_create_raises_mlmmj_error_on_nonzero_exit(tmp_path: Path) -> None:
    a = _adapter(tmp_path)
    with patch("postino_core.adapters.mlmmj.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="bad args"
        )
        with pytest.raises(MlmmjError) as exc:
            a.create(address="team@lists.example.org", primary_owner="alice@example.org")
    assert "bad args" in str(exc.value)
    assert "exit 2" in str(exc.value)


def test_create_raises_mlmmj_error_on_timeout(tmp_path: Path) -> None:
    a = _adapter(tmp_path)
    with patch("postino_core.adapters.mlmmj.subprocess.run") as run:
        run.side_effect = subprocess.TimeoutExpired(cmd="mlmmj-make-ml", timeout=5.0)
        with pytest.raises(MlmmjError) as exc:
            a.create(address="team@lists.example.org", primary_owner="alice@example.org")
    assert "timeout" in str(exc.value).lower()


def test_create_drops_privileges_when_uid_gid_set(tmp_path: Path) -> None:
    a = MlmmjAdapter(spool_root=tmp_path, mlmmj_uid=1234, mlmmj_gid=5678, timeout=5.0)
    with patch("postino_core.adapters.mlmmj.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        a.create(address="team@lists.example.org", primary_owner="alice@example.org")
    _, kwargs = run.call_args
    assert "preexec_fn" in kwargs
    assert callable(kwargs["preexec_fn"])


def test_append_owner_creates_owner_file_if_absent(tmp_path: Path) -> None:
    listdir = tmp_path / "team@lists.example.org"
    (listdir / "control").mkdir(parents=True)
    a = _adapter(tmp_path)
    a.append_owner(address="team@lists.example.org", owner="bob@example.org")
    contents = (listdir / "control" / "owner").read_text()
    assert contents == "bob@example.org\n"


def test_append_owner_appends_when_file_exists(tmp_path: Path) -> None:
    listdir = tmp_path / "team@lists.example.org"
    (listdir / "control").mkdir(parents=True)
    (listdir / "control" / "owner").write_text("alice@example.org\n")
    a = _adapter(tmp_path)
    a.append_owner(address="team@lists.example.org", owner="bob@example.org")
    contents = (listdir / "control" / "owner").read_text()
    assert contents.splitlines() == ["alice@example.org", "bob@example.org"]


def test_append_owner_idempotent_on_duplicate(tmp_path: Path) -> None:
    listdir = tmp_path / "team@lists.example.org"
    (listdir / "control").mkdir(parents=True)
    (listdir / "control" / "owner").write_text("alice@example.org\n")
    a = _adapter(tmp_path)
    a.append_owner(address="team@lists.example.org", owner="alice@example.org")
    contents = (listdir / "control" / "owner").read_text()
    assert contents.splitlines() == ["alice@example.org"]


def test_append_owner_raises_not_found_if_listdir_missing(tmp_path: Path) -> None:
    a = _adapter(tmp_path)
    with pytest.raises(NotFoundError):
        a.append_owner(address="missing@lists.example.org", owner="alice@example.org")
