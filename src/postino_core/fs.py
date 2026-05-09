"""Filesystem adapter for maildir create/remove.

vmail_uid/gid of -1 means "do not chown" (used in unit tests where the
caller has no privileges to chown to the production vmail user)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from postino_core.errors import FilesystemError


class FilesystemAdapter:
    def __init__(self, *, mail_root: Path, vmail_uid: int, vmail_gid: int) -> None:
        self._mail_root = mail_root.resolve()
        self._uid = vmail_uid
        self._gid = vmail_gid

    def create_maildir(self, relative: Path) -> None:
        target = self._safe_join(relative)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise FilesystemError(f"mkdir {target} failed: {e}") from e
        if self._uid >= 0 and self._gid >= 0:
            try:
                os.chown(target, self._uid, self._gid)
            except OSError as e:
                raise FilesystemError(f"chown {target} failed: {e}") from e

    def remove_maildir(self, relative: Path) -> None:
        target = self._safe_join(relative)
        if not target.exists():
            return
        try:
            shutil.rmtree(target)
        except OSError as e:
            raise FilesystemError(f"rmtree {target} failed: {e}") from e

    def _safe_join(self, relative: Path) -> Path:
        joined = (self._mail_root / relative).resolve()
        if not str(joined).startswith(str(self._mail_root)):
            raise FilesystemError(
                f"path traversal: {relative!r} escapes mail_root {self._mail_root}"
            )
        return joined
