"""Filesystem adapter for maildir create/remove.

vmail_uid/gid of -1 means "do not chown" (used in unit tests where the
caller has no privileges to chown to the production vmail user).

postino runs as root in production. This module therefore refuses to
follow symlinks at any step:

- containment uses ``Path.is_relative_to`` (not string-prefix, which
  would let a sibling ``/var/mail2/foo`` masquerade as ``/var/mail/foo``);
- ``_safe_join`` ``lstat``-s every component and refuses any pre-existing
  symlink (defends create-path chmod/chown);
- ``os.chown`` is invoked with ``follow_symlinks=False`` so a symlink
  racing into the tree between ``lstat`` and the ownership write cannot
  redirect it;
- tree removal uses ``shutil.rmtree`` which on POSIX is implemented via
  fd-based ``_rmtree_safe_fd`` (opens each subdir with ``O_NOFOLLOW`` —
  symlinks under the tree are unlinked in place, never descended into).
  We assert ``shutil.rmtree.avoids_symlink_attacks`` at import to fail
  loudly on any platform where the safe variant is unavailable.

Memory ``feedback_destructive_ops.md`` documents a prior real incident
with destructive ops on chrooted paths."""

from __future__ import annotations

import os
import secrets
import shutil
import stat
from pathlib import Path

from postino_core.errors import FilesystemError

assert shutil.rmtree.avoids_symlink_attacks, (
    "shutil.rmtree on this platform does not avoid symlink attacks; "
    "postino refuses to run unsafely under root"
)

_MAILDIR_MODE = 0o700
_MAILDIRPP_SUBDIRS = ("cur", "new", "tmp")

# Prefix for the two-phase delete graveyard. Same convention as
# `MlmmjAdapter._DELETING_PREFIX` so `check/consistency.py` can detect
# partial-delete artefacts uniformly across maildir and mlmmj spool trees.
DELETING_PREFIX = ".deleting."


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
        # _safe_join already lstat'd every component, so `path` is
        # guaranteed not to be a symlink — no need (and no portable way
        # on Linux) to pass ``follow_symlinks=False`` to chmod here.
        os.chmod(path, _MAILDIR_MODE)
        if self._uid >= 0 and self._gid >= 0:
            # ``follow_symlinks=False`` is supported on Linux via lchown
            # and defends against a symlink racing into the tree between
            # the lstat in _safe_join and the chown here.
            os.chown(path, self._uid, self._gid, follow_symlinks=False)

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

    def stage_maildir_for_delete(self, relative: Path) -> Path | None:
        """Atomically rename ``relative`` to a ``.deleting.<token>`` sibling
        and return its absolute path. Returns None if ``relative`` is absent.

        Two-phase delete pattern: callers run this *inside* the DB
        transaction — an atomic ``os.rename`` on the same filesystem is
        the only filesystem op the tx needs to depend on. The full rmtree
        runs outside the tx via ``purge_staged_maildir``. A mid-rmtree
        crash leaves a ``.deleting.*`` artefact that ``postino check --deep``
        can sweep, instead of a partially-wiped maildir restored over
        by a DB rollback (the prior contract's data-loss window).

        Raises ``FilesystemError`` if the rename itself fails (cross-FS,
        EACCES). On rename failure, the DB tx rolls back via the surrounding
        ``engine.begin()`` and the maildir is untouched."""
        target = self._safe_join(relative)
        if not target.exists():
            return None
        staged_name = f"{DELETING_PREFIX}{target.name}.{os.getpid()}.{secrets.token_hex(4)}"
        staged = target.parent / staged_name
        try:
            os.rename(target, staged)
        except OSError as e:
            raise FilesystemError(f"stage {target} → {staged} failed: {e}") from e
        return staged

    def purge_staged_maildir(self, staged: Path) -> None:
        """``rmtree`` a ``.deleting.<token>`` graveyard from
        ``stage_maildir_for_delete``. Idempotent on absent path.

        Raises ``FilesystemError`` if rmtree fails. The DB tx has already
        committed by the time this is called — callers must log the error
        and surface to the operator (the ``.deleting.*`` tree will then
        be visible to ``postino check --deep``)."""
        if not staged.exists():
            return
        if not staged.name.startswith(DELETING_PREFIX):
            raise FilesystemError(
                f"refusing to purge non-graveyard path {staged} "
                f"(expected name prefix {DELETING_PREFIX!r})"
            )
        if not staged.is_relative_to(self._mail_root):
            raise FilesystemError(f"refusing to purge {staged} outside mail_root")
        try:
            shutil.rmtree(staged)
        except OSError as e:
            raise FilesystemError(f"purge_staged_maildir {staged} failed: {e}") from e
        # Best-effort parent cleanup, same as ``remove_maildir``.
        p = staged.parent
        while p != self._mail_root and p != p.parent:
            try:
                p.rmdir()
            except OSError:
                break
            p = p.parent

    def _safe_join(self, relative: Path) -> Path:
        """Resolve ``relative`` inside ``mail_root`` without following
        any symlink at any intermediate component.

        Refuses:
        - absolute ``relative`` paths.
        - ``..`` segments in ``relative``.
        - any pre-existing component that is a symlink.
        - the result being equal to ``mail_root`` itself.

        Returns the joined path. Containment is enforced via
        ``Path.is_relative_to``; string-prefix matching would let a
        sibling like ``/var/mail2/foo`` masquerade as ``/var/mail/foo``.
        """
        if relative.is_absolute():
            raise FilesystemError(
                f"path traversal: {relative!r} is absolute; expected relative under mail_root"
            )
        parts = relative.parts
        if ".." in parts:
            raise FilesystemError(f"path traversal: {relative!r} contains '..'; rejected")
        # Walk components; lstat each that exists. Pre-existing symlinks
        # at any depth would redirect operations outside the tree.
        cursor = self._mail_root
        for part in parts:
            cursor = cursor / part
            try:
                st = cursor.lstat()
            except FileNotFoundError:
                # Not-yet-existing component is fine — mkdir(parents=True)
                # will create it shortly.
                continue
            except OSError as e:
                raise FilesystemError(f"lstat {cursor} failed: {e}") from e
            if stat.S_ISLNK(st.st_mode):
                raise FilesystemError(
                    f"refusing to follow symlink at {cursor}; maildir tree must contain no symlinks"
                )
        if not cursor.is_relative_to(self._mail_root) or cursor == self._mail_root:
            raise FilesystemError(
                f"path traversal: {relative!r} escapes or equals mail_root {self._mail_root}"
            )
        return cursor
