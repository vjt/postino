"""MlmmjAdapter — wrapper around the mlmmj 1.3+ binaries.

postino shells out to ``mlmmj-sub`` / ``mlmmj-unsub`` / ``mlmmj-list`` —
their flag surface is stable across mlmmj 1.3.x (Debian 12), 1.5.x
(Debian 13, FreeBSD ports), and upstream releases.

List creation (``create``) writes the documented spool-dir layout
directly instead of shelling to ``mlmmj-make-ml``. Reason: that command
is a distro-specific shell wrapper — Debian's variant takes
``-L name -s spooldir`` and reads FQDN/owner/lang from stdin; FreeBSD's
port patches in different flags. The actual on-disk layout (subdirs +
``control/owner`` + ``control/listaddress``) is the stable contract
mlmmj 1.0+ commits to and is what ``mlmmj-receive`` / ``mlmmj-process``
consume. We own the create path, not the layout."""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from pydantic import EmailStr

from postino_core.errors import AlreadyExistsError, FilesystemError, MlmmjError, NotFoundError
from postino_core.models import MailingList

_DEFAULT_TIMEOUT = 30.0
_STDERR_MAX = 512  # truncate noisy mlmmj stderr in error messages

# Spool subdirs created for every list — the mlmmj 1.0+ contract.
# Matches the layout produced by upstream `mlmmj-make-ml`.
_SPOOL_SUBDIRS: tuple[str, ...] = (
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
)

# Candidate locations for the mlmmj text skeleton (per-language list-mail
# templates). Probed in order; first existing wins. None found = create
# the list without text/ contents; mlmmj-receive will then emit raw mail
# without canned bodies for confirmations / bounces.
_TEXT_SKEL_CANDIDATES: tuple[Path, ...] = (
    Path("/usr/local/share/mlmmj/text.skel"),  # FreeBSD ports
    Path("/usr/share/mlmmj/text.skel"),  # Debian / Ubuntu
)
_TEXT_SKEL_DEFAULT_LANG = "en"


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

    def _bin(self, name: str) -> str:
        # mlmmj 1.5.x refuses to run when argv[0] is the bare basename
        # (`mlmmj-sub: All mlmmj binaries have to be invoked with full path`).
        # Resolve via PATH once per call — cheap and avoids stashing stale paths.
        full = shutil.which(name)
        return full if full is not None else name

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
        joined = (self._spool_root / str(address)).resolve()
        if not str(joined).startswith(str(self._spool_root.resolve())):
            raise FilesystemError(
                f"path traversal: {address!r} escapes spool_root {self._spool_root}"
            )
        return joined

    def exists(self, *, address: EmailStr) -> bool:
        """True iff the list spool dir is present. Pure FS, no subprocess."""
        return self._listdir(address).exists()

    # -- create -------------------------------------------------------------

    def create(self, *, address: EmailStr, primary_owner: EmailStr) -> None:
        """Lay out the mlmmj spool dir for ``address``.

        Equivalent to ``mlmmj-make-ml`` minus its distro-specific stdin
        prompts. Writes:

        - ``<spool_root>/<address>/{incoming,queue,...,nomailsubs.d}/``
        - ``<spool_root>/<address>/index`` (empty)
        - ``control/owner`` = ``primary_owner``
        - ``control/listaddress`` = ``address``
        - ``text/*`` copied from the first existing
          ``/{usr/local,usr}/share/mlmmj/text.skel/en/`` (skipped silently
          if no skeleton dir is found — the list still works, just without
          canned reply templates).

        Then chowns the whole tree to ``(mlmmj_uid, mlmmj_gid)`` when both
        are non-negative.

        Raises:
            AlreadyExistsError: spool dir already present.
            FilesystemError: mkdir/write/chown failed; partial state is
                rolled back via ``shutil.rmtree``.
        """
        listdir = self._listdir(address)
        if listdir.exists():
            raise AlreadyExistsError(f"mlmmj list already exists at {listdir}")

        try:
            listdir.mkdir(parents=True)
            for sub in _SPOOL_SUBDIRS:
                (listdir / sub).mkdir(parents=True)
            (listdir / "index").touch()
            (listdir / "control" / "owner").write_text(f"{primary_owner}\n", encoding="utf-8")
            (listdir / "control" / "listaddress").write_text(f"{address}\n", encoding="utf-8")
            self._copy_text_skel(listdir / "text")
            self._chown_tree(listdir)
        except OSError as e:
            shutil.rmtree(listdir, ignore_errors=True)
            raise FilesystemError(f"mlmmj create {address} failed: {e}") from e

    def _copy_text_skel(self, dest: Path) -> None:
        """Copy ``text.skel/<lang>/*`` into ``dest`` if the skeleton exists.

        Silent on missing skeleton — mlmmj-receive degrades to raw mail
        bodies, which is acceptable for fresh lists."""
        for root in _TEXT_SKEL_CANDIDATES:
            skel = root / _TEXT_SKEL_DEFAULT_LANG
            if skel.is_dir():
                for f in skel.iterdir():
                    if f.is_file():
                        shutil.copy2(f, dest / f.name)
                return

    def _chown_tree(self, listdir: Path) -> None:
        """Recursively chown to ``(uid, gid)`` when both are non-negative."""
        if self._uid < 0 or self._gid < 0:
            return
        os.chown(listdir, self._uid, self._gid)
        for dirpath, dirnames, filenames in os.walk(listdir):
            for name in dirnames + filenames:
                os.chown(os.path.join(dirpath, name), self._uid, self._gid)

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

    # -- subscriber management ----------------------------------------------

    def subscribe(self, *, address: EmailStr, email: EmailStr) -> None:
        """Run ``mlmmj-sub -L <listdir> -a <email> -s -c -f``.

        Idempotent at the binary level (``-f`` makes already-subscribed a 0-exit)."""
        listdir = self._listdir(address)
        if not listdir.exists():
            raise NotFoundError(f"mlmmj list {address} does not exist")
        cmd = [
            self._bin("mlmmj-sub"),
            "-L",
            str(listdir),
            "-a",
            str(email),
            "-s",  # silent: no welcome mail
            "-c",  # bypass confirmation
            "-f",  # force: don't reject already-subscribed
        ]
        result = self._run(cmd)
        if result.returncode != 0:
            self._raise_mlmmj(cmd, result)

    def unsubscribe(self, *, address: EmailStr, email: EmailStr) -> None:
        """Run ``mlmmj-unsub -L <listdir> -a <email> -s -c``.

        Idempotent: not-subscribed is a 0-exit when ``-c`` is set."""
        listdir = self._listdir(address)
        if not listdir.exists():
            raise NotFoundError(f"mlmmj list {address} does not exist")
        cmd = [
            self._bin("mlmmj-unsub"),
            "-L",
            str(listdir),
            "-a",
            str(email),
            "-s",
            "-c",
        ]
        result = self._run(cmd)
        if result.returncode != 0:
            self._raise_mlmmj(cmd, result)

    # -- read ---------------------------------------------------------------

    def get(self, *, address: EmailStr) -> MailingList | None:
        """Read owners + subscriber count for one list.

        Returns None if the spool dir is missing.
        Raises MlmmjError if ``mlmmj-list`` fails."""
        listdir = self._listdir(address)
        if not listdir.exists():
            return None
        owners = self._read_owners(listdir)
        subscribers = self._read_subscribers(listdir)
        return MailingList(
            address=str(address),
            owners=owners,
            subscriber_count=len(subscribers),
            spool_dir=listdir,
        )

    def list_all(self, *, domain: str | None = None) -> list[MailingList]:
        """Scan ``spool_root`` for list dirs, optionally filtered by FQDN.

        A directory counts as a list if it contains ``control/owner``."""
        if not self._spool_root.exists():
            return []
        out: list[MailingList] = []
        for child in sorted(self._spool_root.iterdir()):
            if not child.is_dir():
                continue
            if not (child / "control" / "owner").exists():
                continue
            address = child.name
            if domain is not None:
                _, _, fqdn = address.partition("@")
                if fqdn != domain:
                    continue
            owners = self._read_owners(child)
            subscribers = self._read_subscribers(child)
            out.append(
                MailingList(
                    address=address,
                    owners=owners,
                    subscriber_count=len(subscribers),
                    spool_dir=child,
                )
            )
        return out

    def _read_owners(self, listdir: Path) -> list[str]:
        owner_file = listdir / "control" / "owner"
        if not owner_file.exists():
            return []
        return [ln.strip() for ln in owner_file.read_text().splitlines() if ln.strip()]

    def _read_subscribers(self, listdir: Path) -> list[str]:
        cmd = [self._bin("mlmmj-list"), "-L", str(listdir)]
        result = self._run(cmd)
        if result.returncode != 0:
            self._raise_mlmmj(cmd, result)
        return [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]

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
