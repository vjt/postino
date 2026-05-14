"""Unit tests for MlmmjAdapter. Pure subprocess.run mocking — no real
mlmmj binaries needed; CI installs the real binary and the integration
suite exercises it."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from postino_core.adapters.mlmmj import MlmmjAdapter
from postino_core.errors import AlreadyExistsError, FilesystemError, MlmmjError, NotFoundError


@pytest.fixture(autouse=True)
def _stub_which(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]  # WHY: autouse pytest fixture is invoked by the framework, not directly referenced.
    """Make ``shutil.which`` deterministic so the post-v0.5 hardening
    that raises ``MlmmjError`` on missing binaries doesn't trip the
    unit suite on CI runners without mlmmj installed. Subprocess calls
    are still mocked separately by each test."""

    def _which(name: str) -> str:
        return f"/usr/bin/{name}"

    monkeypatch.setattr("postino_core.adapters.mlmmj.shutil.which", _which)


def _adapter(tmp_path: Path) -> MlmmjAdapter:
    return MlmmjAdapter(
        spool_root=tmp_path,
        mlmmj_uid=-1,
        mlmmj_gid=-1,
        timeout=5.0,
    )


def test_create_writes_full_spool_layout(tmp_path: Path) -> None:
    a = _adapter(tmp_path)
    a.create(address="team@lists.example.org", primary_owner="alice@example.org")
    listdir = tmp_path / "lists.example.org" / "team"
    for sub in (
        "incoming",
        "queue",
        "queue/discarded",
        "archive",
        "text",
        "subconf",
        "unsubconf",
        "bounce",
        "control",
        "moderation",
        "subscribers.d",
        "digesters.d",
        "requeue",
        "nomailsubs.d",
    ):
        assert (listdir / sub).is_dir(), f"missing subdir: {sub}"
    assert (listdir / "index").exists()
    assert (listdir / "control" / "owner").read_text() == "alice@example.org\n"
    assert (listdir / "control" / "listaddress").read_text() == "team@lists.example.org\n"


def test_create_raises_already_exists_on_existing_dir(tmp_path: Path) -> None:
    listdir = tmp_path / "lists.example.org" / "team"
    listdir.mkdir(parents=True)
    a = _adapter(tmp_path)
    with pytest.raises(AlreadyExistsError):
        a.create(address="team@lists.example.org", primary_owner="alice@example.org")


def test_create_rolls_back_partial_state_on_oserror(tmp_path: Path) -> None:
    a = _adapter(tmp_path)
    # Make spool_root non-writable to trigger OSError partway through layout.
    with patch.object(Path, "mkdir") as mkdir:
        mkdir.side_effect = [None, OSError("disk full")]
        with pytest.raises(FilesystemError) as exc:
            a.create(address="team@lists.example.org", primary_owner="alice@example.org")
    assert "disk full" in str(exc.value)
    assert not (tmp_path / "lists.example.org" / "team").exists()


def test_create_chowns_when_uid_gid_set(tmp_path: Path) -> None:
    a = MlmmjAdapter(spool_root=tmp_path, mlmmj_uid=1234, mlmmj_gid=5678, timeout=5.0)
    with patch("postino_core.adapters.mlmmj.os.chown") as chown:
        a.create(address="team@lists.example.org", primary_owner="alice@example.org")
    # Top-level dir + 14 subdirs + 1 index + 2 control files = 18 entries minimum.
    # We assert all chown calls used the configured uid/gid pair.
    assert chown.call_count >= 18
    for call in chown.call_args_list:
        _, uid, gid = call.args
        assert uid == 1234
        assert gid == 5678


def test_create_no_chown_when_uid_gid_negative(tmp_path: Path) -> None:
    a = _adapter(tmp_path)
    with patch("postino_core.adapters.mlmmj.os.chown") as chown:
        a.create(address="team@lists.example.org", primary_owner="alice@example.org")
    chown.assert_not_called()


def test_listdir_rejects_address_with_slash(tmp_path: Path) -> None:
    """An EmailStr can technically carry a quoted ``/`` in its local part;
    treat it as path-traversal."""
    a = _adapter(tmp_path)
    with pytest.raises(FilesystemError, match="invalid path"):
        a.exists(address="bad/foo@lists.example.org")


def test_listdir_refuses_symlinked_listdir(tmp_path: Path) -> None:
    """A symlinked spool entry would let mlmmj writes redirect outside."""
    outside = tmp_path / "outside"
    outside.mkdir()
    domain_dir = tmp_path / "lists.example.org"
    domain_dir.mkdir()
    (domain_dir / "evil").symlink_to(outside)
    a = _adapter(tmp_path)
    with pytest.raises(FilesystemError, match="symlink"):
        a.exists(address="evil@lists.example.org")


def test_bin_raises_mlmmjerror_when_binary_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_bin`` must surface a typed MlmmjError when which() returns None
    rather than letting subprocess.run inherit a bare basename and fail
    opaquely under mlmmj 1.5.x's full-path requirement."""

    def _which_none(name: str) -> None:
        del name

    monkeypatch.setattr("postino_core.adapters.mlmmj.shutil.which", _which_none)
    a = _adapter(tmp_path)
    (tmp_path / "lists.example.org" / "team").mkdir(parents=True)
    with pytest.raises(MlmmjError, match="not found on PATH"):
        a.subscribe(address="team@lists.example.org", email="bob@example.org")


def test_create_rolls_back_on_non_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Earlier rollback caught only OSError. A non-OSError raised mid-
    create (e.g. unforeseen exception from _chown_tree) must still leave
    no partial spool dir behind."""

    def raise_other(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated unexpected exception")

    monkeypatch.setattr("postino_core.adapters.mlmmj.MlmmjAdapter._chown_tree", raise_other)
    a = MlmmjAdapter(spool_root=tmp_path, mlmmj_uid=1234, mlmmj_gid=5678, timeout=5.0)
    with pytest.raises(RuntimeError, match="simulated unexpected exception"):
        a.create(address="team@lists.example.org", primary_owner="alice@example.org")
    assert not (tmp_path / "lists.example.org" / "team").exists()


def test_append_owner_creates_owner_file_if_absent(tmp_path: Path) -> None:
    listdir = tmp_path / "lists.example.org" / "team"
    (listdir / "control").mkdir(parents=True)
    a = _adapter(tmp_path)
    a.append_owner(address="team@lists.example.org", owner="bob@example.org")
    contents = (listdir / "control" / "owner").read_text()
    assert contents == "bob@example.org\n"


def test_append_owner_appends_when_file_exists(tmp_path: Path) -> None:
    listdir = tmp_path / "lists.example.org" / "team"
    (listdir / "control").mkdir(parents=True)
    (listdir / "control" / "owner").write_text("alice@example.org\n")
    a = _adapter(tmp_path)
    a.append_owner(address="team@lists.example.org", owner="bob@example.org")
    contents = (listdir / "control" / "owner").read_text()
    assert contents.splitlines() == ["alice@example.org", "bob@example.org"]


def test_append_owner_idempotent_on_duplicate(tmp_path: Path) -> None:
    listdir = tmp_path / "lists.example.org" / "team"
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


def test_listdir_rejects_path_traversal(tmp_path: Path) -> None:
    """Defence-in-depth: address with .. must not escape spool_root."""
    a = _adapter(tmp_path)
    with pytest.raises(FilesystemError):
        # Pydantic EmailStr would normally block this; adapter must defend itself too.
        a._listdir("../escape@x.org")  # pyright: ignore[reportPrivateUsage]  # WHY: direct _listdir access from test bypasses pyright's private-method enforcement.


def test_exists_returns_true_when_listdir_present(tmp_path: Path) -> None:
    listdir = tmp_path / "lists.example.org" / "team"
    (listdir / "control").mkdir(parents=True)
    a = _adapter(tmp_path)
    assert a.exists(address="team@lists.example.org") is True


def test_exists_returns_false_when_listdir_absent(tmp_path: Path) -> None:
    a = _adapter(tmp_path)
    assert a.exists(address="missing@lists.example.org") is False


def test_delete_removes_spool_dir(tmp_path: Path) -> None:
    listdir = tmp_path / "lists.example.org" / "team"
    (listdir / "subscribers.d").mkdir(parents=True)
    (listdir / "subscribers.d" / "alice@example.org").write_text("")
    a = _adapter(tmp_path)
    a.delete(address="team@lists.example.org")
    assert not listdir.exists()


def test_create_lays_down_two_level_tree(tmp_path: Path) -> None:
    """create() must produce <spool>/<domain>/<localpart> — two-level path."""
    adapter = _adapter(tmp_path)
    adapter.create(address="team@lists.example.org", primary_owner="alice@example.org")
    base = tmp_path / "lists.example.org" / "team"
    assert (base / "incoming").is_dir()
    assert (base / "control" / "owner").read_text() == "alice@example.org\n"


def test_delete_raises_not_found_if_missing(tmp_path: Path) -> None:
    a = _adapter(tmp_path)
    with pytest.raises(NotFoundError):
        a.delete(address="missing@lists.example.org")


def test_subscribe_invokes_mlmmj_sub_with_correct_argv(tmp_path: Path) -> None:
    listdir = tmp_path / "lists.example.org" / "team"
    listdir.mkdir(parents=True)
    a = _adapter(tmp_path)
    with patch("postino_core.adapters.mlmmj.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        a.subscribe(address="team@lists.example.org", email="bob@example.org")
    cmd = run.call_args[0][0]
    # cmd[0] is the absolute path returned by shutil.which when the binary is
    # installed, else the bare basename. Compare via Path.name to be portable.
    assert Path(cmd[0]).name == "mlmmj-sub"
    assert "-L" in cmd
    assert str(listdir) in cmd
    assert "-a" in cmd
    assert "bob@example.org" in cmd
    assert "-f" in cmd  # force: bypass moderation
    assert "-q" in cmd  # quiet: no owner notification
    assert "-s" in cmd  # silent re-sub
    # -c (Send welcome mail) MUST NOT be passed — v0.10.3 regression guard.
    # See mlmmj-sub(1): "To ensure subscription is silent from the point of
    # view of the subscriber, use -f, but neither -c nor -C."
    assert "-c" not in cmd
    assert "-C" not in cmd


def test_subscribe_raises_not_found_if_listdir_missing(tmp_path: Path) -> None:
    a = _adapter(tmp_path)
    with pytest.raises(NotFoundError):
        a.subscribe(address="missing@lists.example.org", email="bob@example.org")


def test_subscribe_raises_mlmmj_error_on_nonzero(tmp_path: Path) -> None:
    listdir = tmp_path / "lists.example.org" / "team"
    listdir.mkdir(parents=True)
    a = _adapter(tmp_path)
    with patch("postino_core.adapters.mlmmj.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="invalid email"
        )
        with pytest.raises(MlmmjError):
            a.subscribe(address="team@lists.example.org", email="bob@example.org")


def test_unsubscribe_invokes_mlmmj_unsub_with_correct_argv(tmp_path: Path) -> None:
    listdir = tmp_path / "lists.example.org" / "team"
    listdir.mkdir(parents=True)
    a = _adapter(tmp_path)
    with patch("postino_core.adapters.mlmmj.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        a.unsubscribe(address="team@lists.example.org", email="bob@example.org")
    cmd = run.call_args[0][0]
    assert Path(cmd[0]).name == "mlmmj-unsub"
    assert "-L" in cmd
    assert str(listdir) in cmd
    assert "-a" in cmd
    assert "bob@example.org" in cmd
    assert "-q" in cmd  # quiet: no owner notification
    assert "-s" in cmd  # silent re-unsub
    # -c (Send goodbye mail) MUST NOT be passed — v0.10.3 regression guard.
    # See mlmmj-unsub(1): "When neither -c nor -C is specified, unsubscription
    # happens silently from the point of view of the subscriber."
    assert "-c" not in cmd
    assert "-C" not in cmd


def test_unsubscribe_raises_not_found_if_listdir_missing(tmp_path: Path) -> None:
    a = _adapter(tmp_path)
    with pytest.raises(NotFoundError):
        a.unsubscribe(address="missing@lists.example.org", email="bob@example.org")


def _seed_list(spool: Path, addr: str, owners: list[str]) -> Path:
    """Seed a list spool dir using the v0.10 two-level layout: <spool>/<domain>/<localpart>/."""
    _, _, domain = addr.rpartition("@")
    localpart = addr[: addr.rindex("@")]
    listdir = spool / domain / localpart
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
    assert ml.spool_dir == tmp_path / "lists.example.org" / "team"


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


def test_list_all_walks_two_level_layout(tmp_path: Path) -> None:
    # Manually lay out: <spool>/lists.example.org/team/control/owner
    #                   <spool>/example.org/soci/control/owner
    (tmp_path / "lists.example.org" / "team" / "control").mkdir(parents=True)
    (tmp_path / "lists.example.org" / "team" / "control" / "owner").write_text(
        "alice@example.org\n"
    )
    (tmp_path / "example.org" / "soci" / "control").mkdir(parents=True)
    (tmp_path / "example.org" / "soci" / "control" / "owner").write_text("bob@example.org\n")
    # Use a stub for mlmmj-list binary; patch _read_subscribers to return []
    adapter = MlmmjAdapter(spool_root=tmp_path, mlmmj_uid=-1, mlmmj_gid=-1)
    adapter._read_subscribers = lambda listdir: []  # pyright: ignore[reportPrivateUsage]  # WHY: monkeypatching private method in test to avoid subprocess invocation.

    all_lists = adapter.list_all()
    addrs = {ml.address for ml in all_lists}
    assert addrs == {"team@lists.example.org", "soci@example.org"}

    filtered = adapter.list_all(domain="lists.example.org")
    assert {ml.address for ml in filtered} == {"team@lists.example.org"}


def test_list_all_skips_deleting_sentinels(tmp_path: Path) -> None:
    (tmp_path / "lists.example.org" / "team" / "control").mkdir(parents=True)
    (tmp_path / "lists.example.org" / "team" / "control" / "owner").write_text("a@b.c\n")
    (tmp_path / ".deleting.x").mkdir()
    (tmp_path / "lists.example.org" / ".deleting.y" / "control").mkdir(parents=True)
    (tmp_path / "lists.example.org" / ".deleting.y" / "control" / "owner").write_text("a@b.c\n")
    adapter = MlmmjAdapter(spool_root=tmp_path, mlmmj_uid=-1, mlmmj_gid=-1)
    adapter._read_subscribers = lambda listdir: []  # pyright: ignore[reportPrivateUsage]  # WHY: monkeypatching private method in test to avoid subprocess invocation.
    addrs = {ml.address for ml in adapter.list_all()}
    assert addrs == {"team@lists.example.org"}


# ---------------------------------------------------------------------------
# Task 10: _listdir two-level path (<spool>/<domain>/<localpart>/)
# ---------------------------------------------------------------------------


def test_listdir_composes_domain_localpart(tmp_path: Path) -> None:
    adapter = MlmmjAdapter(
        spool_root=tmp_path,
        mlmmj_uid=-1,
        mlmmj_gid=-1,
    )
    p = adapter._listdir("team@lists.example.org")  # pyright: ignore[reportPrivateUsage]  # WHY: direct _listdir access from test bypasses pyright's private-method enforcement.
    assert p == tmp_path / "lists.example.org" / "team"


def test_listdir_rejects_traversal_in_domain(tmp_path: Path) -> None:
    adapter = MlmmjAdapter(spool_root=tmp_path, mlmmj_uid=-1, mlmmj_gid=-1)
    with pytest.raises(FilesystemError):
        adapter._listdir("team@../escape.example.org")  # pyright: ignore[reportPrivateUsage]  # WHY: direct _listdir access from test bypasses pyright's private-method enforcement.


def test_listdir_rejects_traversal_in_localpart(tmp_path: Path) -> None:
    adapter = MlmmjAdapter(spool_root=tmp_path, mlmmj_uid=-1, mlmmj_gid=-1)
    with pytest.raises(FilesystemError):
        # /  in local-part — split would create a third path component.
        adapter._listdir("te/am@lists.example.org")  # pyright: ignore[reportPrivateUsage]  # WHY: direct _listdir access from test bypasses pyright's private-method enforcement.


def test_listdir_rejects_symlink_at_domain_level(tmp_path: Path) -> None:
    adapter = MlmmjAdapter(spool_root=tmp_path, mlmmj_uid=-1, mlmmj_gid=-1)
    real = tmp_path / "real_domain"
    real.mkdir()
    (tmp_path / "lists.example.org").symlink_to(real)
    with pytest.raises(FilesystemError):
        adapter._listdir("team@lists.example.org")  # pyright: ignore[reportPrivateUsage]  # WHY: direct _listdir access from test bypasses pyright's private-method enforcement.


def test_listdir_rejects_symlink_at_localpart_level(tmp_path: Path) -> None:
    adapter = MlmmjAdapter(spool_root=tmp_path, mlmmj_uid=-1, mlmmj_gid=-1)
    (tmp_path / "lists.example.org").mkdir()
    real = tmp_path / "real_list"
    real.mkdir()
    (tmp_path / "lists.example.org" / "team").symlink_to(real)
    with pytest.raises(FilesystemError):
        adapter._listdir("team@lists.example.org")  # pyright: ignore[reportPrivateUsage]  # WHY: direct _listdir access from test bypasses pyright's private-method enforcement.
