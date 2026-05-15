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

import grp
import hashlib
import hmac
import os
import pwd
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import MetaData, select, text
from sqlalchemy.engine import Engine

from postino_core.config import PostinoSettings, parse_postfix_sql_cf
from postino_core.enums import IdentityBackend
from postino_core.errors import ConfigError
from postino_core.fs import DELETING_PREFIX

Severity = Literal["info", "warn", "error"]

_REQUIRED_TABLES = frozenset({"mailbox", "alias", "domain", "quota2", "log"})

_CfPolicy = Literal["always", "if_alias_domain_nonempty"]
# Postfix sql-virtual_*.cf files tracked by `postino check`, paired with
# the policy that decides whether each is mandatory.
#
# * ``always`` — required regardless of DB state (core PostfixAdmin map
#   files). Missing → error.
# * ``if_alias_domain_nonempty`` — required only when the
#   ``alias_domain`` table has at least one row (i.e. the operator
#   actually uses domain aliasing). Missing-while-not-required is silent;
#   missing-while-required is an error. Present-and-matching always
#   produces info, regardless of policy.
_POSTFIX_CF_FILES: tuple[tuple[str, _CfPolicy], ...] = (
    ("sql-virtual_mailbox_maps.cf", "always"),
    ("sql-virtual_alias_maps.cf", "always"),
    ("sql-virtual_domains.cf", "always"),
    ("sql-virtual_alias_alias_domain_maps.cf", "if_alias_domain_nonempty"),
    ("sql-virtual_mailbox_alias_domain_maps.cf", "if_alias_domain_nonempty"),
)
# Canonical filename → tuple of legacy filenames historically accepted.
# When the canonical file is missing but a legacy alias is present,
# postino accepts the legacy file but emits a deprecation warning so
# the operator can rename at their leisure.
#
# v0.10.2: switched from ``sql-virtual_domain_maps.cf`` (singular noun
# + ``_maps`` suffix) to ``sql-virtual_domains.cf`` (plural, no suffix)
# to match the postfix parameter name ``virtual_mailbox_domains``. The
# ``_maps`` suffix is reserved for recipient→target lookups
# (mailbox_maps, alias_maps); domains is a yes/no membership lookup,
# so the bare plural is the right name. Legacy singular accepted as a
# transition aid and to keep existing PostfixAdmin deploys working.
_POSTFIX_CF_LEGACY_NAMES: dict[str, tuple[str, ...]] = {
    "sql-virtual_domains.cf": ("sql-virtual_domain_maps.cf",),
}
_MAILDIRPP_SUBDIRS = ("cur", "new", "tmp")
_HOOK_WRITE_BITS = 0o022
# A4-A4.4: `sql-virtual_*.cf` files carry the cleartext SQL password.
# Postfix's canonical layout is mode 0o640 owner root group postfix —
# postfix's worker uid needs read on the group bit but MUST NOT be
# able to rewrite the file (else any process in group `postfix`,
# including a future helper or compromised sidecar, can inject SQL
# credentials and postino check would stay green until postfix
# reload). Forbid group-write, group-exec, and ALL others bits.
# Tightened from the prior 0o007-only mask which allowed group-w/x.
_CF_FORBIDDEN_BITS = 0o037


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
    findings.extend(_check_vmail_identity(settings))
    findings.extend(_check_postfix_sql_cfs(settings, engine))
    if settings.mlmmj_spool_dir is not None:
        main_cf = settings.postfix_sql_dir.parent / "main.cf"
        master_cf = settings.postfix_sql_dir.parent / "master.cf"
        findings.extend(check_postfix_transport_maps(main_cf))
        findings.extend(check_recipient_delimiter(main_cf))
        findings.extend(check_master_cf_mlmmj_pipes(master_cf))
        findings.extend(check_owner_aliases_for_routes(engine, metadata))
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


def _check_vmail_identity(s: PostinoSettings) -> list[Finding]:
    """Resolve vmail_uid and vmail_gid to a local user/group.

    Two findings emitted (one per axis). Unknown uid/gid is an error
    (postino's FS ops will fail). A name that does not match the
    conventional ``vmail`` is a warn — common typo-defence, not a
    correctness rule.
    """
    out: list[Finding] = []
    try:
        pw_name = pwd.getpwuid(s.vmail_uid).pw_name
    except KeyError:
        out.append(_err("vmail_uid", f"uid={s.vmail_uid} does not resolve to any local user"))
    else:
        if pw_name != "vmail":
            out.append(
                _warn("vmail_uid", f"uid={s.vmail_uid} resolves to {pw_name!r}, expected 'vmail'")
            )
        else:
            out.append(_ok("vmail_uid", f"{s.vmail_uid} → 'vmail'"))
    try:
        gr_name = grp.getgrgid(s.vmail_gid).gr_name
    except KeyError:
        out.append(_err("vmail_gid", f"gid={s.vmail_gid} does not resolve to any local group"))
    else:
        if gr_name != "vmail":
            out.append(
                _warn("vmail_gid", f"gid={s.vmail_gid} resolves to {gr_name!r}, expected 'vmail'")
            )
        else:
            out.append(_ok("vmail_gid", f"{s.vmail_gid} → 'vmail'"))
    return out


def _alias_domain_has_rows(engine: Engine) -> bool:
    """Cheap existence probe for the ``alias_domain`` table.

    Drives the conditional-cf policy in ``_check_postfix_sql_cfs``: the
    two ``*_alias_domain_maps.cf`` files are only mandatory when at
    least one alias_domain row exists.
    """
    with engine.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM alias_domain")).scalar_one()
    return int(n) > 0


def _check_postfix_sql_cfs(s: PostinoSettings, engine: Engine) -> list[Finding]:
    """Verify each postfix sql-virtual_*.cf is present AND matches engine.url.

    Postfix is the source of truth. Any drift between the file and the
    engine postino is currently using is a config-correctness bug.

    Two cf files (``sql-virtual_alias_alias_domain_maps.cf`` and
    ``sql-virtual_mailbox_alias_domain_maps.cf``) are required only when
    the ``alias_domain`` table is non-empty. When the operator does not
    use domain aliasing those files' absence is silent — no finding is
    emitted. Present-and-matching always produces info, regardless of
    policy.
    """
    out: list[Finding] = []
    alias_domain_nonempty = _alias_domain_has_rows(engine)
    for filename, policy in _POSTFIX_CF_FILES:
        cf = s.postfix_sql_dir / filename
        name = f"postfix_sql_cf:{filename}"
        required = policy == "always" or alias_domain_nonempty
        if not cf.exists():
            # Try legacy filenames before declaring missing. If found,
            # accept and emit a deprecation warning so the operator can
            # rename without breaking running mail flow.
            legacy_match: Path | None = None
            legacy_match_name: str | None = None
            for legacy in _POSTFIX_CF_LEGACY_NAMES.get(filename, ()):
                candidate = s.postfix_sql_dir / legacy
                if candidate.exists():
                    legacy_match = candidate
                    legacy_match_name = legacy
                    break
            if legacy_match is None:
                if required:
                    out.append(_err(name, f"postfix sql cf missing: {cf}"))
                # else: silently skip — file is not required for this deployment.
                continue
            out.append(
                Finding(
                    name=name,
                    severity="warn",
                    message=(
                        f"using legacy filename {legacy_match_name!r} for "
                        f"{filename!r}; rename to canonical {filename!r} to "
                        f"match postfix parameter naming. Legacy name will "
                        f"be removed in a future release. ({legacy_match})"
                    ),
                )
            )
            cf = legacy_match
        st = cf.stat()
        bad_bits = st.st_mode & _CF_FORBIDDEN_BITS
        if bad_bits:
            out.append(
                _err(
                    name,
                    f"postfix sql cf has forbidden mode bits "
                    f"(mode={oct(st.st_mode & 0o777)}, forbidden={oct(bad_bits)}); "
                    f"chmod 640 + chown root:postfix to protect the embedded "
                    f"SQL password: {cf}",
                )
            )
            continue
        # Non-root ownership: promoted from warn to error (A4-A4.4).
        # If postino is running as root and the cf is owned by a
        # non-root user, that user can rewrite the credentials at any
        # moment — equivalent to group-write but harder to spot.
        if os.geteuid() == 0 and st.st_uid != 0:
            out.append(
                _err(
                    name,
                    f"postfix sql cf not owned by root: uid={st.st_uid}; "
                    f"chown root:postfix to protect the embedded SQL "
                    f"password: {cf}",
                )
            )
            continue
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
    """Reconcile mlmmj spool tree against known ``domain`` rows.

    Surfaces three drift conditions invisible to the rest of the deep
    check:

    1. Spool dirs under ``mlmmj_spool_dir`` whose ``@<domain>`` portion
       does not match any ``domain`` row (orphan spool — domain was
       deleted but the spool was not cleaned up).
    2. Spool dirs whose ``control/owner`` file is missing/empty
       (corrupt list state).
    3. ``.deleting.*`` / ``.tmp-*`` artefact dirs left behind by a
       partial-delete or partial-create rollback. The current
       MailingListService.delete is FS-first and uses no rename
       sentinel, but operators can move spool dirs aside by hand —
       and a future delete refactor may adopt the rename pattern.

    v0.10: ``domain.transport`` is no longer used for list routing.
    The check matches spool dirs against all ``domain`` rows instead of
    only rows with ``transport='mlmmj'``.

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
            for r in conn.execute(select(domain_t.c.domain)).fetchall()
        }

    out: list[Finding] = []
    # Spool dirs on disk; classify each.
    on_disk: set[str] = set()
    corrupt: list[str] = []
    orphan_address: list[str] = []
    artefacts: list[str] = []
    symlinks: list[str] = []
    try:
        entries = list(spool_root.iterdir())
    except OSError as e:
        return [_warn("mailing_lists", f"cannot scan {spool_root}: {e}")]
    for entry in entries:
        # `is_dir()` follows symlinks; the adapter's `_listdir`
        # refuses any symlink under the spool root, so a symlinked
        # entry here is operator-injected and worth surfacing
        # separately (L2-S11) rather than silently treating it as
        # a list dir.
        if entry.is_symlink():
            symlinks.append(entry.name)
            continue
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
                f"domain row: {orphan_address[:5]}",
            )
        )
    else:
        out.append(_ok("mlmmj_lists_orphan_domain", "all spool dirs map to a known domain"))

    if artefacts:
        out.append(
            _warn(
                "mlmmj_lists_artefacts",
                f"{len(artefacts)} partial-delete/create artefact dir(s): {artefacts[:5]}",
            )
        )
    if symlinks:
        out.append(
            _err(
                "mlmmj_lists_symlinks",
                f"{len(symlinks)} symlink(s) under mlmmj_spool_dir — "
                f"adapter refuses these on read/write, so they are operator-injected "
                f"and represent stale state or a security issue: {symlinks[:5]}",
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
    # Build expected paths without symlink-following. `Path.resolve()`
    # follows every component, which the FS adapter explicitly
    # refuses (fs._safe_join). Using absolute() preserves the literal
    # path layout used at provisioning time (L2-S11).
    expected = {(base / r.maildir).absolute() for r in mailbox_rows}
    orphans: list[str] = []
    artefacts: list[str] = []
    symlinks: list[str] = []
    for domain_dir in base.iterdir():
        if domain_dir.is_symlink():
            symlinks.append(domain_dir.name)
            continue
        if not domain_dir.is_dir():
            continue
        if domain_dir.name.startswith(DELETING_PREFIX):
            # Per-domain rmtree graveyard from a failed
            # DomainService.delete post-commit purge. Surfaced
            # separately so operators can rmtree it explicitly.
            artefacts.append(domain_dir.name)
            continue
        for local_dir in domain_dir.iterdir():
            if local_dir.is_symlink():
                symlinks.append(f"{domain_dir.name}/{local_dir.name}")
                continue
            if not local_dir.is_dir():
                continue
            if local_dir.name.startswith(DELETING_PREFIX):
                artefacts.append(f"{domain_dir.name}/{local_dir.name}")
                continue
            if local_dir.absolute() not in expected:
                orphans.append(f"{domain_dir.name}/{local_dir.name}")
    out: list[Finding] = []
    if orphans:
        out.append(
            _err(
                "orphan_maildirs",
                f"{len(orphans)} maildirs on disk without DB row: {orphans[:5]}",
            )
        )
    else:
        out.append(_ok("orphan_maildirs", "no orphan maildirs on disk"))
    if artefacts:
        out.append(
            _warn(
                "maildir_artefacts",
                f"{len(artefacts)} partial-delete .deleting.* tree(s) "
                f"left from a post-commit purge failure: {artefacts[:5]}",
            )
        )
    if symlinks:
        out.append(
            _err(
                "maildir_symlinks",
                f"{len(symlinks)} symlink(s) under virtual_mailbox_base — "
                f"adapter refuses these, so they are operator-injected and "
                f"represent stale state or a security issue: {symlinks[:5]}",
            )
        )
    return out


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
    # `.deleting.*` graveyards are surfaced by `_check_orphan_maildirs` as
    # `maildir_artefacts`; skip them here so they don't double-flag as
    # `orphan_domain_maildirs`. Symlinks are surfaced by
    # `_check_orphan_maildirs` as `maildir_symlinks`; skip too.
    orphans = sorted(
        d.name
        for d in base.iterdir()
        if not d.is_symlink()
        and d.is_dir()
        and not d.name.startswith(DELETING_PREFIX)
        and d.name not in domain_names
    )
    if orphans:
        return [
            _err(
                "orphan_domain_maildirs",
                f"{len(orphans)} per-domain maildir tree(s) without "
                f"a matching domain row: {orphans[:5]}",
            )
        ]
    return [_ok("orphan_domain_maildirs", "no orphan per-domain maildirs on disk")]


def check_postfix_transport_maps(main_cf: Path) -> list[Finding]:
    """Validate main.cf transport_maps wiring for v0.10+ mlmmj routing.

    Required: ``transport_maps = mysql:<routes-cf>, mysql:<virtual-transport-cf>``
    in that order. The routes source MUST appear FIRST so per-list
    regex patterns win over per-domain catchall.
    """
    if not main_cf.exists():
        return [
            Finding(
                name="postfix-main-cf",
                severity="error",
                message=f"main.cf not found at {main_cf}",
            )
        ]
    content = main_cf.read_text()
    line = next(
        (
            ln.split("=", 1)[1].strip()
            for ln in content.splitlines()
            if ln.strip().startswith("transport_maps")
        ),
        None,
    )
    if line is None:
        return [
            Finding(
                name="postfix-transport-maps",
                severity="error",
                message=(
                    "main.cf: transport_maps is not set; v0.10 requires "
                    "transport_maps = mysql:sql-routes.cf, mysql:sql-virtual_transport.cf"
                ),
            )
        ]
    sources = [s.strip() for s in line.split(",")]
    findings: list[Finding] = []
    if len(sources) < 2:
        findings.append(
            Finding(
                name="postfix-transport-maps",
                severity="error",
                message=(
                    f"main.cf: transport_maps has only {len(sources)} source(s); "
                    "v0.10 requires both mysql:sql-routes.cf and mysql:sql-virtual_transport.cf"
                ),
            )
        )
        return findings
    first, second = sources[0], sources[1]
    if "routes" not in first:
        findings.append(
            Finding(
                name="postfix-transport-maps-order",
                severity="error",
                message=(
                    f"main.cf: first transport_maps source must reference routes ('routes' "
                    f"in path); got {first!r}. Per-list patterns must win over per-domain catchall."
                ),
            )
        )
    if not (first.startswith("mysql:") and second.startswith("mysql:")):
        findings.append(
            Finding(
                name="postfix-transport-maps-type",
                severity="error",
                message=(
                    f"main.cf: both transport_maps sources must be mysql:; "
                    f"got {first!r}, {second!r}"
                ),
            )
        )
    if not findings:
        findings.append(
            Finding(
                name="postfix-transport-maps",
                severity="info",
                message=f"transport_maps OK: {first}, {second}",
            )
        )
    return findings


_REQUIRED_MASTER_CF_PIPES = (
    "mlmmj-receive",
    "mlmmj-bounce",
    "mlmmj-sub",
    "mlmmj-unsub",
)


def check_master_cf_mlmmj_pipes(master_cf: Path) -> list[Finding]:
    """Validate master.cf has the 4 v0.10 mlmmj pipe service blocks.

    Help requests (`list+help@domain`) are handled by `mlmmj-receive -e
    help` via plus-addressing, not by a separate `mlmmj-help` binary
    (which doesn't exist in mlmmj 1.3+ in Debian/Ubuntu/FreeBSD pkg).
    See repos/routes.py for the routing rationale.
    """
    if not master_cf.exists():
        return [
            Finding(
                name="master-cf",
                severity="error",
                message=f"master.cf not found at {master_cf}",
            )
        ]
    text = master_cf.read_text()
    findings: list[Finding] = []
    for name in _REQUIRED_MASTER_CF_PIPES:
        # service blocks start with `<name> unix ... pipe`
        if not any(ln.split() and ln.split()[0] == name for ln in text.splitlines()):
            findings.append(
                Finding(
                    name=f"master-cf-{name}",
                    severity="error",
                    message=f"master.cf missing service block: {name}",
                )
            )
        else:
            findings.append(
                Finding(
                    name=f"master-cf-{name}",
                    severity="info",
                    message=f"master.cf has {name}",
                )
            )
    return findings


def check_owner_aliases_for_routes(engine: Engine, md: MetaData) -> list[Finding]:
    """For every distinct list_address in routes, confirm a matching
    ``<localpart>-owner@<domain>`` alias row exists in the alias table.

    A mailing list without an ``-owner`` alias means bounce messages and
    owner-directed mail (e.g. ``list-owner@domain``) have no delivery target.
    This surfaces as severity=error so the operator can create the alias
    before going live.

    Returns a single info finding when the routes table is empty or all
    lists have their -owner alias.
    """
    routes_t = md.tables.get("routes")
    alias_t = md.tables.get("alias")
    if routes_t is None or alias_t is None:
        return [
            _err("owner-aliases", "routes or alias table missing — cannot verify -owner aliases")
        ]
    findings: list[Finding] = []
    with engine.connect() as conn:
        list_addrs = conn.execute(
            select(routes_t.c.list_address).where(routes_t.c.list_address.is_not(None)).distinct()
        ).fetchall()
        for (la,) in list_addrs:
            la_str = str(la)
            localpart, _, domain = la_str.partition("@")
            owner_addr = f"{localpart}-owner@{domain}"
            row = conn.execute(
                select(alias_t.c.address).where(alias_t.c.address == owner_addr)
            ).fetchone()
            if row is None:
                findings.append(
                    Finding(
                        name=f"owner-alias-{la_str}",
                        severity="error",
                        message=(
                            f"missing -owner alias row for list {la_str}; "
                            f"expected alias.address={owner_addr!r}"
                        ),
                    )
                )
    if not findings:
        findings.append(
            Finding(
                name="owner-aliases",
                severity="info",
                message="all routes have matching -owner aliases",
            )
        )
    return findings


def check_recipient_delimiter(main_cf: Path) -> list[Finding]:
    """v0.10 needs recipient_delimiter to contain both `+` (mailbox
    subaddressing) and `-` (mlmmj hyphen-suffix dispatch)."""
    if not main_cf.exists():
        return []
    text = main_cf.read_text()
    line = next(
        (
            ln.split("=", 1)[1].strip()
            for ln in text.splitlines()
            if ln.strip().startswith("recipient_delimiter")
        ),
        None,
    )
    if line is None:
        return [
            Finding(
                name="recipient-delimiter",
                severity="error",
                message=(
                    "main.cf: recipient_delimiter is not set; v0.10 requires "
                    "recipient_delimiter = +-"
                ),
            )
        ]
    if "+" not in line or "-" not in line:
        return [
            Finding(
                name="recipient-delimiter",
                severity="error",
                message=(
                    f"main.cf: recipient_delimiter must contain both '+' and '-'; got {line!r}"
                ),
            )
        ]
    return [
        Finding(
            name="recipient-delimiter",
            severity="info",
            message=f"recipient_delimiter OK: {line}",
        )
    ]
