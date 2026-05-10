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
