"""Filesystem adapter for maildir create/remove.

vmail_uid/gid of -1 means "do not chown" (used in unit tests where the
caller has no privileges to chown to the production vmail user)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from postino_core.errors import FilesystemError

_MAILDIR_MODE = 0o700
_MAILDIRPP_SUBDIRS = ("cur", "new", "tmp")


class FilesystemAdapter:
    def __init__(self, *, mail_root: Path, vmail_uid: int, vmail_gid: int) -> None:
        self._mail_root = mail_root.resolve()
        self._uid = vmail_uid
        self._gid = vmail_gid

    def create_maildir(self, relative: Path) -> None:
        target = self._safe_join(relative)

        # Walk upward from the target, collecting paths that do not yet
        # exist. These are the directories `mkdir(parents=True)` is about
        # to create, and that we therefore own (perms + rollback).
        fresh: list[Path] = []
        cursor = target
        while cursor != self._mail_root and cursor != cursor.parent and not cursor.exists():
            fresh.append(cursor)
            cursor = cursor.parent
        # Topmost freshly-created path; used as rollback root.
        rollback_root = fresh[-1] if fresh else None

        try:
            target.mkdir(parents=True, exist_ok=True)
            # Always enforce 0o700 on the maildir itself, even if it
            # pre-existed — operator may have left it world-readable.
            self._lock_down(target)
            # Apply perms to freshly-created parent dirs (skip target,
            # already done above; skip any pre-existing parent).
            for d in fresh:
                if d == target:
                    continue
                self._lock_down(d)
            # Maildir++ skeleton inside target.
            for sub in _MAILDIRPP_SUBDIRS:
                sd = target / sub
                sd.mkdir(exist_ok=True)
                self._lock_down(sd)
        except OSError as e:
            if rollback_root is not None and rollback_root.exists():
                shutil.rmtree(rollback_root, ignore_errors=True)
            raise FilesystemError(f"create_maildir {target} failed: {e}") from e

    def _lock_down(self, path: Path) -> None:
        os.chmod(path, _MAILDIR_MODE)
        if self._uid >= 0 and self._gid >= 0:
            os.chown(path, self._uid, self._gid)

    def maildir_exists(self, relative: Path) -> bool:
        return self._safe_join(relative).exists()

    def remove_maildir(self, relative: Path) -> None:
        target = self._safe_join(relative)
        if target.exists():
            try:
                shutil.rmtree(target)
            except OSError as e:
                raise FilesystemError(f"rmtree {target} failed: {e}") from e
        # Best-effort: remove now-empty parent dirs up to (but excluding)
        # mail_root. Stops at the first non-empty parent. Keeps the tree
        # tidy when the last mailbox in a domain is removed and prevents
        # orphan per-domain dirs after add-rollback.
        p = target.parent
        while p != self._mail_root and p != p.parent:
            try:
                p.rmdir()
            except OSError:
                break
            p = p.parent

    def _safe_join(self, relative: Path) -> Path:
        joined = (self._mail_root / relative).resolve()
        if not str(joined).startswith(str(self._mail_root)):
            raise FilesystemError(
                f"path traversal: {relative!r} escapes mail_root {self._mail_root}"
            )
        return joined
