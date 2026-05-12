"""postino check — consistency validator (read-only).

Two modes:

* default — environmental preconditions: DB reachable, schema present,
  mail_root mounted, postcreation hook executable + owned by root +
  not world/group writable, postfix sql `.cf` files present and the
  credentials in them match the engine URL postino is using.

* `--deep` — actual state-drift detection: mailbox rows reconciled
  against maildirs on disk, quota2 row presence, FK substitutes
  (mailbox.domain ∈ domain, alias.domain ∈ domain), maildir ownership
  matches `vmail_uid:vmail_gid`, Maildir++ skeleton (`cur`/`new`/`tmp`)
  exists.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import MetaData, select, text
from sqlalchemy.engine import Engine

from postino_core.config import PostinoSettings, parse_postfix_sql_cf
from postino_core.enums import IdentityBackend
from postino_core.errors import ConfigError

Severity = Literal["info", "warn", "error"]

_REQUIRED_TABLES = frozenset({"mailbox", "alias", "domain", "quota2", "log"})
_POSTFIX_CF_FILES = (
    "sql-virtual_mailbox_maps.cf",
    "sql-virtual_alias_maps.cf",
    "sql-virtual_domain_maps.cf",
)
_MAILDIRPP_SUBDIRS = ("cur", "new", "tmp")
_HOOK_WRITE_BITS = 0o022
# A4.1: `sql-virtual_*.cf` files carry the cleartext SQL password.
# Postfix's canonical layout is mode 0o640 owner root group postfix —
# postfix's worker uid needs read access. Forbid only the OTHERS bits
# (0o007 = others r/w/x); group-read is legit and required.
_CF_OTHERS_BITS = 0o007


class Finding(BaseModel):
    """One row in a `postino check` report."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str
    severity: Severity
    message: str

    @property
    def ok(self) -> bool:
        return self.severity == "info"


class CheckResult(BaseModel):
    """Aggregate of all findings produced by `run_consistency_check`."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    findings: list[Finding]

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)


def run_consistency_check(
    *,
    settings: PostinoSettings,
    engine: Engine,
    metadata: MetaData,
    deep: bool = False,
) -> CheckResult:
    findings: list[Finding] = []
    findings.append(_check_db_reachable(engine))
    findings.append(_check_required_tables(metadata))
    findings.append(_check_mailbox_base(settings))
    findings.append(_check_postcreation_hook(settings))
    findings.extend(_check_postfix_sql_cfs(settings, engine))
    if deep:
        findings.extend(_check_deep(settings, engine, metadata))
    return CheckResult(findings=findings)


def _ok(name: str, message: str) -> Finding:
    return Finding(name=name, severity="info", message=message)


def _err(name: str, message: str) -> Finding:
    return Finding(name=name, severity="error", message=message)


def _warn(name: str, message: str) -> Finding:
    return Finding(name=name, severity="warn", message=message)


def _check_db_reachable(engine: Engine) -> Finding:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        return _err("db_reachable", f"DB unreachable: {e}")
    return _ok("db_reachable", "DB reachable")


def _check_required_tables(md: MetaData) -> Finding:
    missing = _REQUIRED_TABLES - set(md.tables.keys())
    if missing:
        return _err("schema_tables", f"missing tables: {sorted(missing)}")
    return _ok("schema_tables", "all required tables present")


def _check_mailbox_base(s: PostinoSettings) -> Finding:
    p = s.virtual_mailbox_base
    if not p.is_dir():
        return _err(
            "mailbox_base",
            f"virtual_mailbox_base does not exist or is not a directory: {p}",
        )
    return _ok("mailbox_base", f"{p} exists")


def _check_postcreation_hook(s: PostinoSettings) -> Finding:
    h = s.postcreation_hook
    if not h.exists():
        return _err("postcreation_hook", f"postcreation hook missing: {h}")
    if not os.access(h, os.X_OK):
        return _err("postcreation_hook", f"postcreation hook not executable: {h}")
    st = h.stat()
    # Ownership is enforced only when postino itself runs as root (the
    # production case). Non-root invocations cannot fix or even meaningfully
    # interpret a "not owned by root" finding, and would otherwise force
    # tests using user-owned tmpfiles to flip an error.
    if os.geteuid() == 0 and st.st_uid != 0:
        return _err(
            "postcreation_hook",
            f"postcreation hook not owned by root: uid={st.st_uid} ({h})",
        )
    if st.st_mode & _HOOK_WRITE_BITS:
        return _err(
            "postcreation_hook",
            f"postcreation hook is group/world writable (mode={oct(st.st_mode & 0o777)}): {h}",
        )
    return _ok("postcreation_hook", f"{h} executable, mode tight")


def _check_postfix_sql_cfs(s: PostinoSettings, engine: Engine) -> list[Finding]:
    """Verify each postfix sql-virtual_*.cf is present AND matches engine.url.

    Postfix is the source of truth. Any drift between the file and the
    engine postino is currently using is a config-correctness bug.
    """
    out: list[Finding] = []
    for filename in _POSTFIX_CF_FILES:
        cf = s.postfix_sql_dir / filename
        name = f"postfix_sql_cf:{filename}"
        if not cf.exists():
            out.append(_err(name, f"postfix sql cf missing: {cf}"))
            continue
        st = cf.stat()
        if st.st_mode & _CF_OTHERS_BITS:
            out.append(
                _err(
                    name,
                    f"postfix sql cf is accessible to 'others' "
                    f"(mode={oct(st.st_mode & 0o777)}); chmod 640 + chown root:postfix "
                    f"to protect the embedded SQL password: {cf}",
                )
            )
            continue
        if os.geteuid() == 0 and st.st_uid != 0:
            out.append(
                _warn(
                    name,
                    f"postfix sql cf not owned by root: uid={st.st_uid} ({cf})",
                )
            )
        try:
            parsed = parse_postfix_sql_cf(cf)
        except ConfigError as e:
            out.append(_err(name, f"unparseable postfix sql cf {cf}: {e}"))
            continue
        url = engine.url
        eu_user = url.username or ""
        eu_host = url.host or ""
        eu_db = url.database or ""
        diffs: list[str] = []
        if parsed.user != eu_user:
            diffs.append(f"user {parsed.user!r}≠engine {eu_user!r}")
        if parsed.host != eu_host:
            diffs.append(f"host {parsed.host!r}≠engine {eu_host!r}")
        if parsed.dbname != eu_db:
            diffs.append(f"dbname {parsed.dbname!r}≠engine {eu_db!r}")
        # Compare hashes rather than cleartexts so the SQL password
        # never appears as a bare `str` on the stack (a `show_locals`
        # traceback or a future logger refactor would otherwise expose
        # it). hmac.compare_digest gives constant-time comparison too.
        parsed_pwd_digest = hashlib.sha256(parsed.password.get_secret_value().encode()).digest()
        eu_pwd_digest = hashlib.sha256((url.password or "").encode()).digest()
        if not hmac.compare_digest(parsed_pwd_digest, eu_pwd_digest):
            diffs.append("password mismatch")
        if diffs:
            out.append(_err(name, f"{cf}: " + ", ".join(diffs)))
        else:
            out.append(_ok(name, f"{cf} matches engine URL"))
    return out


class _MailboxRow(BaseModel):
    """Materialized mailbox tuple used by deep checks.

    Existing as a typed payload lets `_check_deep` push pyright-strict
    SQLAlchemy `Any`-typed accesses to a single conversion point.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    username: str
    maildir: str
    domain: str


_DOVECOT_CONF_DIRS = (
    Path("/etc/dovecot/conf.d"),
    Path("/usr/local/etc/dovecot/conf.d"),
)


def _check_dovecot_passdb_chain(s: PostinoSettings) -> list[Finding]:
    """When identity_backend ∈ {noauth, hybrid}, confirm dovecot has a non-SQL passdb.

    Under noauth every row carries the ``{NOAUTH}`` sentinel; under
    hybrid some rows do. Either way dovecot must chain a non-SQL passdb
    (passwd-file, ldap, pam, static, imap …) behind passdb-sql so the
    sentinel rows resolve. A chain that is only `driver = sql` blocks
    surfaces as `severity=error`; an unreadable config downgrades to
    `warn` and asks the operator to verify manually.
    """
    if s.identity_backend not in (IdentityBackend.NOAUTH, IdentityBackend.HYBRID):
        return []
    auth_files: list[Path] = []
    for d in _DOVECOT_CONF_DIRS:
        if d.is_dir():
            try:
                auth_files.extend(sorted(d.glob("auth-*.conf.ext")))
            except OSError as e:
                return [_warn("dovecot_passdb_chain", f"cannot scan {d}: {e}")]
    if not auth_files:
        return [
            _warn(
                "dovecot_passdb_chain",
                "cannot verify dovecot passdb chain: no auth-*.conf.ext "
                f"found under {[str(p) for p in _DOVECOT_CONF_DIRS]}",
            )
        ]
    drivers: list[str] = []
    for path in auth_files:
        try:
            content = path.read_text()
        except OSError as e:
            return [_warn("dovecot_passdb_chain", f"cannot read {path}: {e}")]
        drivers.extend(_extract_passdb_drivers(content))
    if not drivers:
        return [
            _warn(
                "dovecot_passdb_chain",
                "dovecot config present but no `passdb { driver = ... }` blocks parsed",
            )
        ]
    non_sql = sorted({d for d in drivers if d != "sql"})
    if not non_sql:
        msg = (
            f"identity_backend={s.identity_backend.value} but every dovecot passdb "
            f"uses driver=sql ({sorted(set(drivers))}) — external IdP passdb missing"
        )
        return [_err("dovecot_passdb_chain", msg)]
    return [_ok("dovecot_passdb_chain", f"non-sql passdb present: {non_sql}")]


def _extract_passdb_drivers(content: str) -> list[str]:
    """Pull every `driver = X` line from inside `passdb { ... }` blocks.

    Tracks brace depth so nested braces (rare but legal in dovecot) do
    not confuse the scanner; ignores comments and blank lines.
    """
    drivers: list[str] = []
    depth = 0
    in_passdb = False
    for raw in content.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if not in_passdb:
            if line.startswith("passdb"):
                in_passdb = True
                depth = line.count("{")
            continue
        depth += line.count("{")
        if line.startswith("driver"):
            _, _, rhs = line.partition("=")
            if rhs:
                drivers.append(rhs.strip())
        depth -= line.count("}")
        if depth <= 0:
            in_passdb = False
    return drivers


def _check_deep(s: PostinoSettings, engine: Engine, md: MetaData) -> list[Finding]:
    out: list[Finding] = []
    out.extend(_check_dovecot_passdb_chain(s))
    mailbox_t = md.tables.get("mailbox")
    domain_t = md.tables.get("domain")
    alias_t = md.tables.get("alias")
    quota2_t = md.tables.get("quota2")
    if mailbox_t is None or domain_t is None or alias_t is None or quota2_t is None:
        out.append(_err("deep_skipped", "deep checks skipped: required tables missing"))
        return out

    with engine.connect() as conn:
        mailbox_rows = [
            _MailboxRow(
                username=str(r._mapping["username"]),  # type: ignore[index]  # WHY: SQLAlchemy RowMapping[str, Any] indexing.
                maildir=str(r._mapping["maildir"]),  # type: ignore[index]  # WHY: SQLAlchemy RowMapping[str, Any] indexing.
                domain=str(r._mapping["domain"]),  # type: ignore[index]  # WHY: SQLAlchemy RowMapping[str, Any] indexing.
            )
            for r in conn.execute(
                select(mailbox_t.c.username, mailbox_t.c.maildir, mailbox_t.c.domain)
            ).fetchall()
        ]
        domain_names: set[str] = {
            str(r._mapping["domain"])  # type: ignore[index]  # WHY: SQLAlchemy RowMapping[str, Any] indexing.
            for r in conn.execute(select(domain_t.c.domain)).fetchall()
        }
        alias_rows: list[tuple[str, str]] = [
            (
                str(r._mapping["address"]),  # type: ignore[index]  # WHY: SQLAlchemy RowMapping[str, Any] indexing.
                str(r._mapping["domain"]),  # type: ignore[index]  # WHY: SQLAlchemy RowMapping[str, Any] indexing.
            )
            for r in conn.execute(select(alias_t.c.address, alias_t.c.domain)).fetchall()
        ]
        quota_users: set[str] = {
            str(r._mapping["username"])  # type: ignore[index]  # WHY: SQLAlchemy RowMapping[str, Any] indexing.
            for r in conn.execute(select(quota2_t.c.username)).fetchall()
        }

    out.extend(_check_mailbox_domain_fk(mailbox_rows, domain_names))
    out.extend(_check_alias_domain_fk(alias_rows, domain_names))
    out.extend(_check_quota_pairing(mailbox_rows, quota_users))
    out.extend(_check_mailbox_maildir_pairing(s, mailbox_rows))
    out.extend(_check_orphan_maildirs(s, mailbox_rows))
    out.extend(_check_orphan_domain_maildirs(s, domain_names))
    out.extend(_check_mailing_lists(s, engine, md))
    return out


def _check_mailing_lists(s: PostinoSettings, engine: Engine, md: MetaData) -> list[Finding]:
    """Reconcile mlmmj spool tree against ``domain.transport='mlmmj'`` rows.

    Surfaces three drift conditions invisible to the rest of the deep
    check:

    1. ``domain`` rows with ``transport='mlmmj'`` but missing a spool
       dir (failed list-create that left no FS trace; mail for that
       domain currently bounces).
    2. Spool dirs under ``mlmmj_spool_dir`` whose ``@<domain>`` portion
       does not match any ``domain`` row, or whose ``control/owner``
       file is missing/empty (corrupt list state).
    3. ``.deleting.*`` / ``.tmp-*`` artefact dirs left behind by a
       partial-delete or partial-create rollback. The current
       MailingListService.delete is FS-first and uses no rename
       sentinel, but operators can move spool dirs aside by hand —
       and a future delete refactor may adopt the rename pattern.

    Skipped silently when ``mlmmj_spool_dir`` is not configured.
    """
    spool_root = s.mlmmj_spool_dir
    if spool_root is None:
        return []
    if not spool_root.is_dir():
        return [_warn("mailing_lists", f"mlmmj_spool_dir does not exist: {spool_root}")]

    domain_t = md.tables["domain"]
    with engine.connect() as conn:
        mlmmj_domains = {
            str(r._mapping["domain"])  # type: ignore[index]  # WHY: SQLAlchemy RowMapping[str, Any] indexing.
            for r in conn.execute(
                select(domain_t.c.domain).where(domain_t.c.transport == "mlmmj")
            ).fetchall()
        }

    out: list[Finding] = []
    # Spool dirs on disk; classify each.
    on_disk: set[str] = set()
    corrupt: list[str] = []
    orphan_address: list[str] = []
    artefacts: list[str] = []
    try:
        entries = list(spool_root.iterdir())
    except OSError as e:
        return [_warn("mailing_lists", f"cannot scan {spool_root}: {e}")]
    for entry in entries:
        if not entry.is_dir():
            continue
        name = entry.name
        if name.startswith(".deleting.") or name.startswith(".tmp-"):
            artefacts.append(name)
            continue
        on_disk.add(name)
        owner = entry / "control" / "owner"
        if not owner.exists() or owner.read_text().strip() == "":
            corrupt.append(name)
            continue
        _, _, fqdn = name.partition("@")
        if fqdn not in mlmmj_domains:
            orphan_address.append(name)

    if corrupt:
        out.append(
            _err(
                "mlmmj_lists_corrupt",
                f"{len(corrupt)} spool dir(s) missing/empty control/owner: {corrupt[:5]}",
            )
        )
    else:
        out.append(_ok("mlmmj_lists_corrupt", "all spool dirs have control/owner"))

    if orphan_address:
        out.append(
            _err(
                "mlmmj_lists_orphan_domain",
                f"{len(orphan_address)} spool dir(s) without a matching "
                f"transport=mlmmj domain row: {orphan_address[:5]}",
            )
        )
    else:
        out.append(_ok("mlmmj_lists_orphan_domain", "all spool dirs map to mlmmj domains"))

    if artefacts:
        out.append(
            _warn(
                "mlmmj_lists_artefacts",
                f"{len(artefacts)} partial-delete/create artefact dir(s): {artefacts[:5]}",
            )
        )
    return out


def _check_mailbox_domain_fk(
    mailbox_rows: list[_MailboxRow],
    domain_names: set[str],
) -> list[Finding]:
    orphans = sorted({r.username for r in mailbox_rows if r.domain not in domain_names})
    if orphans:
        return [
            _err(
                "mailbox_domain_fk",
                f"{len(orphans)} mailbox rows reference missing domain: {orphans[:5]}",
            )
        ]
    return [_ok("mailbox_domain_fk", "all mailbox.domain values resolve")]


def _check_alias_domain_fk(
    alias_rows: list[tuple[str, str]],
    domain_names: set[str],
) -> list[Finding]:
    orphans = sorted({addr for addr, dom in alias_rows if dom not in domain_names})
    if orphans:
        return [
            _err(
                "alias_domain_fk",
                f"{len(orphans)} alias rows reference missing domain: {orphans[:5]}",
            )
        ]
    return [_ok("alias_domain_fk", "all alias.domain values resolve")]


def _check_quota_pairing(
    mailbox_rows: list[_MailboxRow],
    quota_users: set[str],
) -> list[Finding]:
    missing = sorted({r.username for r in mailbox_rows if r.username not in quota_users})
    if missing:
        return [
            _err(
                "quota2_pairing",
                f"{len(missing)} mailboxes have no quota2 row: {missing[:5]}",
            )
        ]
    return [_ok("quota2_pairing", "every mailbox has a quota2 row")]


def _check_mailbox_maildir_pairing(
    s: PostinoSettings,
    mailbox_rows: list[_MailboxRow],
) -> list[Finding]:
    """For every mailbox row: maildir on disk + ownership + Maildir++ skeleton."""
    base = s.virtual_mailbox_base
    missing: list[str] = []
    bad_owner: list[str] = []
    bad_skeleton: list[str] = []
    for r in mailbox_rows:
        path = base / r.maildir
        if not path.is_dir():
            missing.append(r.username)
            continue
        if s.vmail_uid >= 0 and s.vmail_gid >= 0:
            st = path.stat()
            if st.st_uid != s.vmail_uid or st.st_gid != s.vmail_gid:
                bad_owner.append(f"{r.username} (uid={st.st_uid},gid={st.st_gid})")
        if any(not (path / sub).is_dir() for sub in _MAILDIRPP_SUBDIRS):
            bad_skeleton.append(r.username)
    out: list[Finding] = []
    if missing:
        out.append(
            _err(
                "maildir_present",
                f"{len(missing)} mailbox rows have no maildir on disk: {missing[:5]}",
            )
        )
    else:
        out.append(_ok("maildir_present", "every mailbox row has a maildir"))
    if bad_owner:
        out.append(
            _err(
                "maildir_ownership",
                f"{len(bad_owner)} maildirs not owned by "
                f"{s.vmail_uid}:{s.vmail_gid}: {bad_owner[:5]}",
            )
        )
    elif s.vmail_uid >= 0 and s.vmail_gid >= 0:
        out.append(_ok("maildir_ownership", "all maildirs owned by vmail"))
    if bad_skeleton:
        out.append(
            _err(
                "maildirpp_skeleton",
                f"{len(bad_skeleton)} maildirs missing cur/new/tmp: {bad_skeleton[:5]}",
            )
        )
    else:
        out.append(_ok("maildirpp_skeleton", "all maildirs have Maildir++ skeleton"))
    return out


def _check_orphan_maildirs(
    s: PostinoSettings,
    mailbox_rows: list[_MailboxRow],
) -> list[Finding]:
    """Maildirs on disk with no DB row.

    Layout per `MailboxService.add`: ``<base>/<domain>/<local_part>/``.
    Anything else under the mail_root is suspicious orphan state.
    """
    base = s.virtual_mailbox_base
    if not base.is_dir():
        return []
    expected = {(base / r.maildir).resolve() for r in mailbox_rows}
    orphans: list[str] = []
    for domain_dir in base.iterdir():
        if not domain_dir.is_dir():
            continue
        for local_dir in domain_dir.iterdir():
            if not local_dir.is_dir():
                continue
            resolved = local_dir.resolve()
            if resolved not in expected:
                orphans.append(f"{domain_dir.name}/{local_dir.name}")
    if orphans:
        return [
            _err(
                "orphan_maildirs",
                f"{len(orphans)} maildirs on disk without DB row: {orphans[:5]}",
            )
        ]
    return [_ok("orphan_maildirs", "no orphan maildirs on disk")]


def _check_orphan_domain_maildirs(
    s: PostinoSettings,
    domain_names: set[str],
) -> list[Finding]:
    """Per-domain maildir trees with no matching `domain` row.

    Catches the privacy-axis bug (review A3.8): a `domain.delete --force`
    that committed the DB cascade but failed FS removal would leave a
    tenant's maildir tree on disk. If the same domain is re-added later
    and a mailbox with the same local-part provisioned, it would adopt
    the old maildir — leaking another tenant's mail.

    The v0.4 DomainService.delete moves rmtree into the transaction so
    this state should not occur going forward, but historical leftovers
    surface here for the operator to disposition.
    """
    base = s.virtual_mailbox_base
    if not base.is_dir():
        return []
    orphans = sorted(d.name for d in base.iterdir() if d.is_dir() and d.name not in domain_names)
    if orphans:
        return [
            _err(
                "orphan_domain_maildirs",
                f"{len(orphans)} per-domain maildir tree(s) without "
                f"a matching domain row: {orphans[:5]}",
            )
        ]
    return [_ok("orphan_domain_maildirs", "no orphan per-domain maildirs on disk")]
