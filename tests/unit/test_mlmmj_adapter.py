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


def test_delete_removes_spool_dir(tmp_path: Path) -> None:
    listdir = tmp_path / "team@lists.example.org"
    (listdir / "subscribers.d").mkdir(parents=True)
    (listdir / "subscribers.d" / "alice@example.org").write_text("")
    a = _adapter(tmp_path)
    a.delete(address="team@lists.example.org")
    assert not listdir.exists()


def test_delete_raises_not_found_if_missing(tmp_path: Path) -> None:
    a = _adapter(tmp_path)
    with pytest.raises(NotFoundError):
        a.delete(address="missing@lists.example.org")


def test_subscribe_invokes_mlmmj_sub_with_correct_argv(tmp_path: Path) -> None:
    listdir = tmp_path / "team@lists.example.org"
    listdir.mkdir()
    a = _adapter(tmp_path)
    with patch("postino_core.adapters.mlmmj.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        a.subscribe(address="team@lists.example.org", email="bob@example.org")
    cmd = run.call_args[0][0]
    assert cmd[0] == "mlmmj-sub"
    assert "-L" in cmd
    assert str(listdir) in cmd
    assert "-a" in cmd
    assert "bob@example.org" in cmd
    assert "-s" in cmd  # silent
    assert "-c" in cmd  # no confirm
    assert "-f" in cmd  # force


def test_subscribe_raises_not_found_if_listdir_missing(tmp_path: Path) -> None:
    a = _adapter(tmp_path)
    with pytest.raises(NotFoundError):
        a.subscribe(address="missing@lists.example.org", email="bob@example.org")


def test_subscribe_raises_mlmmj_error_on_nonzero(tmp_path: Path) -> None:
    listdir = tmp_path / "team@lists.example.org"
    listdir.mkdir()
    a = _adapter(tmp_path)
    with patch("postino_core.adapters.mlmmj.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="invalid email"
        )
        with pytest.raises(MlmmjError):
            a.subscribe(address="team@lists.example.org", email="bob@example.org")


def test_unsubscribe_invokes_mlmmj_unsub_with_correct_argv(tmp_path: Path) -> None:
    listdir = tmp_path / "team@lists.example.org"
    listdir.mkdir()
    a = _adapter(tmp_path)
    with patch("postino_core.adapters.mlmmj.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        a.unsubscribe(address="team@lists.example.org", email="bob@example.org")
    cmd = run.call_args[0][0]
    assert cmd[0] == "mlmmj-unsub"
    assert "-L" in cmd
    assert str(listdir) in cmd
    assert "-a" in cmd
    assert "bob@example.org" in cmd
    assert "-s" in cmd
    assert "-c" in cmd


def test_unsubscribe_raises_not_found_if_listdir_missing(tmp_path: Path) -> None:
    a = _adapter(tmp_path)
    with pytest.raises(NotFoundError):
        a.unsubscribe(address="missing@lists.example.org", email="bob@example.org")


def _seed_list(spool: Path, addr: str, owners: list[str]) -> Path:
    listdir = spool / addr
    (listdir / "control").mkdir(parents=True)
    (listdir / "control" / "owner").write_text("\n".join(owners) + "\n")
    return listdir


def test_get_returns_none_when_listdir_missing(tmp_path: Path) -> None:
    a = _adapter(tmp_path)
    assert a.get(address="missing@lists.example.org") is None


def test_get_parses_owners_and_subscriber_count(tmp_path: Path) -> None:
    _seed_list(tmp_path, "team@lists.example.org", ["alice@example.org", "bob@example.org"])
    a = _adapter(tmp_path)
    with patch("postino_core.adapters.mlmmj.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="carol@example.org\ndan@example.org\n",
            stderr="",
        )
        ml = a.get(address="team@lists.example.org")
    assert ml is not None
    assert ml.address == "team@lists.example.org"
    assert ml.owners == ["alice@example.org", "bob@example.org"]
    assert ml.subscriber_count == 2
    assert ml.spool_dir == tmp_path / "team@lists.example.org"


def test_get_raises_mlmmj_error_when_mlmmj_list_fails(tmp_path: Path) -> None:
    _seed_list(tmp_path, "team@lists.example.org", ["alice@example.org"])
    a = _adapter(tmp_path)
    with patch("postino_core.adapters.mlmmj.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="oops"
        )
        with pytest.raises(MlmmjError):
            a.get(address="team@lists.example.org")


def test_list_all_scans_spool_root(tmp_path: Path) -> None:
    _seed_list(tmp_path, "team@lists.example.org", ["alice@example.org"])
    _seed_list(tmp_path, "ops@lists.example.org", ["carol@example.org"])
    # A non-list dir at the same level — must be skipped.
    (tmp_path / "scratch").mkdir()
    a = _adapter(tmp_path)
    with patch("postino_core.adapters.mlmmj.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        result = a.list_all()
    addrs = sorted(ml.address for ml in result)
    assert addrs == ["ops@lists.example.org", "team@lists.example.org"]


def test_list_all_filters_by_domain(tmp_path: Path) -> None:
    _seed_list(tmp_path, "team@lists.example.org", ["alice@example.org"])
    _seed_list(tmp_path, "ops@lists.other.org", ["carol@example.org"])
    a = _adapter(tmp_path)
    with patch("postino_core.adapters.mlmmj.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        result = a.list_all(domain="lists.example.org")
    addrs = [ml.address for ml in result]
    assert addrs == ["team@lists.example.org"]
