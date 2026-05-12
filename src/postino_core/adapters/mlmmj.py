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
consume. We own the create path, not the layout.

Symlink safety: ``_listdir`` lstat-s every component and refuses any
pre-existing symlink under the spool root. ``shutil.rmtree`` on POSIX
is implemented via fd-based ``_rmtree_safe_fd`` (opens each subdir with
``O_NOFOLLOW``) — symlinks are unlinked in place rather than descended
into. ``os.chown`` uses ``follow_symlinks=False`` so a symlink racing
into the freshly-created tree cannot redirect ownership outside it.
The ``shutil.rmtree.avoids_symlink_attacks`` invariant is asserted in
``postino_core.fs``."""

from __future__ import annotations

import contextlib
import fcntl
import os
import secrets
import shutil
import stat
import subprocess
from collections.abc import Generator
from pathlib import Path

from pydantic import EmailStr

from postino_core.errors import AlreadyExistsError, FilesystemError, MlmmjError, NotFoundError
from postino_core.models import MailingList

_DEFAULT_TIMEOUT = 30.0
_STDERR_MAX = 512  # truncate noisy mlmmj stderr in error messages
_CREATE_LOCK_NAME = ".create.lock"
_DELETING_PREFIX = ".deleting."

# Minimal env passed to mlmmj subprocesses. The parent process may
# carry POSTINO_*, .env-exported credentials, etc. — none of which
# mlmmj-sub/unsub/list consume. Some mlmmj debug builds log argv
# and environment to syslog; scrub the env block to a clean
# allowlist. (L2-S12)
_MLMMJ_ENV: dict[str, str] = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/sbin:/usr/local/bin",
    "HOME": "/",
    "LANG": "C",
}

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
        # Resolve once at construction so the path-traversal guard does
        # not re-evaluate symlinks under the operator's feet if the
        # spool mount changes mid-run.
        self._spool_root = spool_root.resolve()
        self._uid = mlmmj_uid
        self._gid = mlmmj_gid
        self._timeout = timeout

    def _bin(self, name: str) -> str:
        # mlmmj 1.5.x refuses to run when argv[0] is the bare basename
        # (`mlmmj-sub: All mlmmj binaries have to be invoked with full path`).
        # Resolve via PATH once per call — cheap and avoids stashing stale paths.
        full = shutil.which(name)
        if full is None:
            raise MlmmjError(f"{name}: not found on PATH — install mlmmj or fix PATH")
        return full

    # -- subprocess plumbing ------------------------------------------------

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        # L2-S11: prefer subprocess.run's ``user=`` / ``group=`` /
        # ``extra_groups=`` over ``preexec_fn`` for the uid/gid drop.
        # ``preexec_fn`` runs arbitrary Python in the child between
        # fork() and exec() — unsafe in multi-threaded apps (postinod
        # is one via Litestar/uvicorn). ``user=``/``group=`` perform
        # the drop in the C-level fork helper before any Python code
        # runs in the child. ``extra_groups=[]`` mirrors the previous
        # ``setgroups([])`` so the child does not inherit root's
        # supplementary groups (wheel, mail, …).
        drop_kwargs: dict[str, object] = {}
        if self._uid >= 0 and self._gid >= 0:
            drop_kwargs["user"] = self._uid
            drop_kwargs["group"] = self._gid
            drop_kwargs["extra_groups"] = []
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
                env=_MLMMJ_ENV,
                cwd="/",
                **drop_kwargs,  # type: ignore[arg-type]  # WHY: subprocess.run typing rejects a dict[str, object] splat; values are str|int per drop_kwargs construction above
            )
        except subprocess.TimeoutExpired as e:
            raise MlmmjError(f"{cmd[0]}: timeout after {self._timeout}s") from e

    def _raise_mlmmj(self, cmd: list[str], result: subprocess.CompletedProcess[str]) -> None:
        stderr = result.stderr.strip()[:_STDERR_MAX]
        raise MlmmjError(f"{cmd[0]}: exit {result.returncode}: {stderr}")

    def _listdir(self, address: EmailStr) -> Path:
        """Compose ``<spool_root>/<address>`` with path-traversal and
        symlink defenses.

        Rejects:
        - addresses containing a ``/`` (the local-part of an EmailStr can
          legally contain quoted slashes; we refuse them because they
          would split the path component).
        - addresses containing ``..`` segments.
        - results that escape ``spool_root`` (e.g. via prefix sibling
          directories: ``/var/spool/mlmmj2/foo`` for a root of
          ``/var/spool/mlmmj``).
        - results that equal ``spool_root`` itself.
        - any pre-existing component that is a symlink (symlinks under
          the spool root would let an attacker redirect mlmmj writes).
        """
        addr_str = str(address)
        if "/" in addr_str or addr_str in ("..", ".") or addr_str.startswith("."):
            raise FilesystemError(f"path traversal: {address!r} contains invalid path characters")
        joined = self._spool_root / addr_str
        # Component-by-component lstat: refuse any symlink under the
        # spool root rather than calling resolve() (which follows them).
        cursor = self._spool_root
        for part in (addr_str,):
            cursor = cursor / part
            try:
                st = cursor.lstat()
            except FileNotFoundError:
                continue
            except OSError as e:
                raise FilesystemError(f"lstat {cursor} failed: {e}") from e
            if stat.S_ISLNK(st.st_mode):
                raise FilesystemError(
                    f"refusing to follow symlink at {cursor}; "
                    f"mlmmj spool tree must contain no symlinks"
                )
        if not joined.is_relative_to(self._spool_root) or joined == self._spool_root:
            raise FilesystemError(
                f"path traversal: {address!r} escapes or equals spool_root {self._spool_root}"
            )
        return joined

    def exists(self, *, address: EmailStr) -> bool:
        """True iff the list spool dir is present. Pure FS, no subprocess."""
        return self._listdir(address).exists()

    # -- create -------------------------------------------------------------

    @contextlib.contextmanager
    def _create_lock(self) -> Generator[None]:
        """Serialize concurrent ``create`` / ``delete`` calls under a
        single OS-level flock on ``<spool_root>/.create.lock``.

        Eliminates the two-process race where both pass the
        ``listdir.exists()`` check and one's compensation ``rmtree``
        deletes the other's freshly-created spool tree."""
        self._spool_root.mkdir(parents=True, exist_ok=True)
        lock_path = self._spool_root / _CREATE_LOCK_NAME
        # `os.open` with O_CREAT|O_RDWR is race-safe; mode 0o600 because
        # this file lives at spool root and we want to restrict it to
        # mlmmj's uid (chown done lazily below when uid/gid configured).
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

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

        The whole sequence runs under ``_create_lock`` so concurrent
        creators serialize and the loser-deletes-winner race window
        documented in the v0.6 codebase review (A3.2) closes.

        Raises:
            AlreadyExistsError: spool dir already present.
            FilesystemError: mkdir/write/chown failed; partial state is
                rolled back via ``shutil.rmtree``.
        """
        with self._create_lock():
            listdir = self._listdir(address)
            if listdir.exists():
                raise AlreadyExistsError(f"mlmmj list already exists at {listdir}")

            try:
                listdir.mkdir(parents=True)
                for sub in _SPOOL_SUBDIRS:
                    (listdir / sub).mkdir(parents=True)
                (listdir / "index").touch()
                self._atomic_write_text(listdir / "control" / "owner", f"{primary_owner}\n")
                self._atomic_write_text(listdir / "control" / "listaddress", f"{address}\n")
                self._copy_text_skel(listdir / "text")
                self._chown_tree(listdir)
            except Exception as e:
                # Catch broader than OSError so KeyboardInterrupt / MlmmjError /
                # any unexpected exception still rolls the partial tree back.
                # Rollback errors are surfaced (no ``ignore_errors=True``): a
                # silent rollback failure on a read-only mount or quota-exhausted
                # FS would leak a half-built spool dir and block retries with
                # AlreadyExistsError.
                try:
                    shutil.rmtree(listdir)
                except OSError as rollback_err:
                    raise FilesystemError(
                        f"mlmmj create {address} failed: {e}; AND rollback failed: {rollback_err}"
                    ) from e
                if isinstance(e, OSError):
                    raise FilesystemError(f"mlmmj create {address} failed: {e}") from e
                raise

    def _atomic_write_text(self, target: Path, content: str) -> None:
        """Write ``content`` to ``target`` via tempfile + fsync + rename.

        Avoids the half-written ``control/owner`` corruption the
        consistency checker flags at ``check/consistency.py:397``: a
        truncating ``open(target, 'w')`` is interrupted between
        ``O_TRUNC`` and the final write, leaving an empty file."""
        tmp = target.with_name(f"{target.name}.tmp.{os.getpid()}.{secrets.token_hex(4)}")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except OSError:
            # Best-effort cleanup of the partial tempfile; let the
            # original error propagate.
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise

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
        """Recursively chown to ``(uid, gid)`` when both are non-negative.

        Uses ``follow_symlinks=False`` (lchown) so a symlink racing into
        the freshly-created tree cannot redirect ownership onto a file
        outside the spool root.
        """
        if self._uid < 0 or self._gid < 0:
            return
        os.chown(listdir, self._uid, self._gid, follow_symlinks=False)
        for dirpath, dirnames, filenames in os.walk(listdir, followlinks=False):
            for name in dirnames + filenames:
                os.chown(
                    os.path.join(dirpath, name),
                    self._uid,
                    self._gid,
                    follow_symlinks=False,
                )

    # -- delete -------------------------------------------------------------

    def delete(self, *, address: EmailStr) -> None:
        """Remove the list spool dir.

        Two-phase delete: rename the spool dir to a ``.deleting.*``
        sentinel name first (atomic), then ``rmtree``. A partial
        ``rmtree`` failure leaves the ``.deleting.*`` artefact —
        ``check/consistency.py:392`` already anticipates this prefix
        and surfaces it for operator cleanup — rather than a
        half-removed list with the original name (which the
        consistency check would flag as ``corrupt`` mailing list).

        The rename happens under ``_create_lock`` to serialize against
        concurrent ``create`` calls for the same address.

        Raises:
            NotFoundError: spool dir does not exist.
            FilesystemError: rename or rmtree failed.
        """
        with self._create_lock():
            listdir = self._listdir(address)
            if not listdir.exists():
                raise NotFoundError(f"mlmmj list {address} does not exist")
            graveyard = listdir.with_name(
                f"{_DELETING_PREFIX}{listdir.name}.{os.getpid()}.{secrets.token_hex(4)}"
            )
            try:
                os.rename(listdir, graveyard)
            except OSError as e:
                raise FilesystemError(f"rename {listdir} → {graveyard} failed: {e}") from e
        try:
            shutil.rmtree(graveyard)
        except OSError as e:
            raise FilesystemError(f"rmtree {graveyard} failed: {e}") from e

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
            # Skip dot-prefixed sentinels: ``.deleting.*`` rename graveyard
            # from a partial delete, ``.create.lock`` directory if ever
            # created in error. The consistency checker handles surfacing
            # these to the operator.
            if child.name.startswith("."):
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
