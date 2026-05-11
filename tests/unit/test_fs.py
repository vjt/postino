import os
from pathlib import Path

import pytest

from postino_core.errors import FilesystemError
from postino_core.fs import FilesystemAdapter


def test_create_maildir(tmp_path: Path) -> None:
    fs = FilesystemAdapter(mail_root=tmp_path, vmail_uid=-1, vmail_gid=-1)
    fs.create_maildir(Path("example.com/foo/"))
    assert (tmp_path / "example.com" / "foo").is_dir()


def test_create_maildir_idempotent(tmp_path: Path) -> None:
    fs = FilesystemAdapter(mail_root=tmp_path, vmail_uid=-1, vmail_gid=-1)
    fs.create_maildir(Path("example.com/foo/"))
    fs.create_maildir(Path("example.com/foo/"))  # no error


def test_remove_maildir(tmp_path: Path) -> None:
    fs = FilesystemAdapter(mail_root=tmp_path, vmail_uid=-1, vmail_gid=-1)
    fs.create_maildir(Path("example.com/foo/"))
    fs.remove_maildir(Path("example.com/foo/"))
    assert not (tmp_path / "example.com" / "foo").exists()


def test_remove_maildir_idempotent(tmp_path: Path) -> None:
    fs = FilesystemAdapter(mail_root=tmp_path, vmail_uid=-1, vmail_gid=-1)
    fs.remove_maildir(Path("example.com/foo/"))


def test_path_traversal_rejected(tmp_path: Path) -> None:
    fs = FilesystemAdapter(mail_root=tmp_path, vmail_uid=-1, vmail_gid=-1)
    with pytest.raises(FilesystemError):
        fs.create_maildir(Path("../escape/"))


def test_create_maildir_target_mode_is_0700(tmp_path: Path) -> None:
    fs = FilesystemAdapter(mail_root=tmp_path, vmail_uid=-1, vmail_gid=-1)
    fs.create_maildir(Path("example.com/foo/"))
    target = tmp_path / "example.com" / "foo"
    assert (target.stat().st_mode & 0o777) == 0o700


def test_create_maildir_creates_maildirpp_skeleton(tmp_path: Path) -> None:
    fs = FilesystemAdapter(mail_root=tmp_path, vmail_uid=-1, vmail_gid=-1)
    fs.create_maildir(Path("example.com/foo/"))
    target = tmp_path / "example.com" / "foo"
    for sub in ("cur", "new", "tmp"):
        sd = target / sub
        assert sd.is_dir()
        assert (sd.stat().st_mode & 0o777) == 0o700


def test_create_maildir_chmods_freshly_created_parent(tmp_path: Path) -> None:
    fs = FilesystemAdapter(mail_root=tmp_path, vmail_uid=-1, vmail_gid=-1)
    fs.create_maildir(Path("newdomain.example.org/foo/"))
    parent = tmp_path / "newdomain.example.org"
    assert (parent.stat().st_mode & 0o777) == 0o700


def test_create_maildir_does_not_chmod_existing_parent(tmp_path: Path) -> None:
    parent = tmp_path / "example.com"
    parent.mkdir(mode=0o755)
    # Force perms (mkdir respects umask; explicit chmod ensures the test
    # starts at the documented baseline regardless of test-runner umask).
    os.chmod(parent, 0o755)
    fs = FilesystemAdapter(mail_root=tmp_path, vmail_uid=-1, vmail_gid=-1)
    fs.create_maildir(Path("example.com/foo/"))
    assert (parent.stat().st_mode & 0o777) == 0o755


def test_create_maildir_rolls_back_freshly_created_parent_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fs = FilesystemAdapter(mail_root=tmp_path, vmail_uid=os.getuid(), vmail_gid=os.getgid())

    def fail_chown(path: object, uid: int, gid: int, *, follow_symlinks: bool = True) -> None:
        del follow_symlinks
        raise OSError("simulated chown EPERM")

    monkeypatch.setattr("postino_core.fs.os.chown", fail_chown)
    with pytest.raises(FilesystemError):
        fs.create_maildir(Path("freshdomain.example.org/foo/"))
    assert not (tmp_path / "freshdomain.example.org").exists()


def test_create_maildir_rollback_preserves_existing_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "example.com"
    parent.mkdir(mode=0o755)
    fs = FilesystemAdapter(mail_root=tmp_path, vmail_uid=os.getuid(), vmail_gid=os.getgid())

    def fail_chown(path: object, uid: int, gid: int, *, follow_symlinks: bool = True) -> None:
        del follow_symlinks
        raise OSError("simulated chown EPERM")

    monkeypatch.setattr("postino_core.fs.os.chown", fail_chown)
    with pytest.raises(FilesystemError):
        fs.create_maildir(Path("example.com/foo/"))
    assert parent.exists()
    assert not (parent / "foo").exists()


def test_safe_join_refuses_absolute(tmp_path: Path) -> None:
    fs = FilesystemAdapter(mail_root=tmp_path, vmail_uid=-1, vmail_gid=-1)
    with pytest.raises(FilesystemError, match="absolute"):
        fs.create_maildir(Path("/etc/passwd"))


def test_safe_join_refuses_symlinked_parent(tmp_path: Path) -> None:
    """A symlinked domain dir would redirect the maildir write outside the tree."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "evil.example.org").symlink_to(outside)
    fs = FilesystemAdapter(mail_root=tmp_path, vmail_uid=-1, vmail_gid=-1)
    with pytest.raises(FilesystemError, match="symlink"):
        fs.create_maildir(Path("evil.example.org/foo/"))
    assert list(outside.iterdir()) == []


def test_remove_maildir_refuses_to_descend_through_symlink(tmp_path: Path) -> None:
    """rmtree must not follow a symlink that points outside the tree."""
    fs = FilesystemAdapter(mail_root=tmp_path, vmail_uid=-1, vmail_gid=-1)
    fs.create_maildir(Path("example.com/foo/"))
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "do-not-touch"
    sentinel.write_text("alive")
    # Plant a symlink inside the maildir pointing at the outside dir.
    (tmp_path / "example.com" / "foo" / "link").symlink_to(outside)
    fs.remove_maildir(Path("example.com/foo/"))
    assert sentinel.read_text() == "alive", "rmtree followed symlink into outside dir"
    assert outside.is_dir()
