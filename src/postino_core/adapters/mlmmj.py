"""MlmmjAdapter — thin subprocess wrapper around the mlmmj 1.3.x binaries.

postino owns the flag surface, not the on-disk format. Every method
shells out to the bundled binaries and parses their output; nothing
in this module touches mlmmj's internal files except ``control/owner``
(documented mlmmj contract since 1.0)."""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from pydantic import EmailStr

from postino_core.errors import AlreadyExistsError, FilesystemError, MlmmjError, NotFoundError

_DEFAULT_TIMEOUT = 30.0
_STDERR_MAX = 512  # truncate noisy mlmmj stderr in error messages


class MlmmjAdapter:
    def __init__(
        self,
        *,
        spool_root: Path,
        mlmmj_uid: int,
        mlmmj_gid: int,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._spool_root = spool_root
        self._uid = mlmmj_uid
        self._gid = mlmmj_gid
        self._timeout = timeout

    # -- subprocess plumbing ------------------------------------------------

    def _preexec(self) -> Callable[[], None] | None:
        if self._uid < 0 or self._gid < 0:
            return None
        uid, gid = self._uid, self._gid

        def _drop() -> None:
            os.setgid(gid)
            os.setuid(uid)

        return _drop

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                preexec_fn=self._preexec(),
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise MlmmjError(f"{cmd[0]}: timeout after {self._timeout}s") from e

    def _raise_mlmmj(self, cmd: list[str], result: subprocess.CompletedProcess[str]) -> None:
        stderr = result.stderr.strip()[:_STDERR_MAX]
        raise MlmmjError(f"{cmd[0]}: exit {result.returncode}: {stderr}")

    def _listdir(self, address: EmailStr) -> Path:
        return self._spool_root / str(address)

    # -- create -------------------------------------------------------------

    def create(self, *, address: EmailStr, primary_owner: EmailStr) -> None:
        """Run ``mlmmj-make-ml -s -L <listdir> -a <addr> -h <fqdn> -o <owner>``.

        Raises:
            AlreadyExistsError: spool dir already present.
            MlmmjError: subprocess exited non-zero or timed out.
        """
        listdir = self._listdir(address)
        if listdir.exists():
            raise AlreadyExistsError(f"mlmmj list already exists at {listdir}")

        _, _, fqdn = str(address).partition("@")
        cmd = [
            "mlmmj-make-ml",
            "-s",  # silent (no interactive prompts)
            "-L",
            str(listdir),
            "-a",
            str(address),
            "-h",
            fqdn,
            "-o",
            str(primary_owner),
        ]
        result = self._run(cmd)
        if result.returncode != 0:
            self._raise_mlmmj(cmd, result)

    # -- delete -------------------------------------------------------------

    def delete(self, *, address: EmailStr) -> None:
        """Remove the list spool dir.

        Raises:
            NotFoundError: spool dir does not exist.
            FilesystemError: rmtree failed (perm or partial-removal race).
        """
        listdir = self._listdir(address)
        if not listdir.exists():
            raise NotFoundError(f"mlmmj list {address} does not exist")
        try:
            shutil.rmtree(listdir)
        except OSError as e:
            raise FilesystemError(f"rmtree {listdir} failed: {e}") from e

    # -- owner management ---------------------------------------------------

    def append_owner(self, *, address: EmailStr, owner: EmailStr) -> None:
        """Append ``owner`` to ``<listdir>/control/owner`` under flock.

        Idempotent: a duplicate owner is a no-op. Raises
        ``NotFoundError`` if the list spool dir is missing."""
        listdir = self._listdir(address)
        if not listdir.exists():
            raise NotFoundError(f"mlmmj list {address} does not exist")
        owner_file = listdir / "control" / "owner"
        owner_file.parent.mkdir(parents=True, exist_ok=True)
        if not owner_file.exists():
            owner_file.touch()
        with owner_file.open("r+", encoding="utf-8") as fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                contents = fh.read()
                existing = {ln.strip() for ln in contents.splitlines() if ln.strip()}
                if str(owner) in existing:
                    return
                if contents and not contents.endswith("\n"):
                    fh.write("\n")
                fh.write(f"{owner}\n")
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
