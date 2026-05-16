"""postino config fix — reconcile a live postfix+dovecot deployment to canonical shape.

Detection: shell out to postconf/doveconf, parse output.
Diff: compare detected dict to canonical target dict, emit human-readable lines + refusals.
Apply: run postconf -e/-X/-Me/-MX, atomic-rename a dovecot fragment file.

No new Pydantic models — detection returns a flat dict[str, str]; refusals
are surfaced as typed exceptions; the renderer (config_gen.generate) owns
the sql cf writes.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
from pathlib import Path

from postino_core.config_gen.input import RenderContext
from postino_core.config_gen.templates import (
    _ENV,  # pyright: ignore[reportPrivateUsage]  # WHY: config fix renders a fix-only template that intentionally lives outside the _REGISTRY exported by templates.py.
)
from postino_core.errors import FixAmbiguity, FixApplyError, FixDetectionFailed


def _which_or_raise(binary: str) -> str:
    path = shutil.which(binary)
    if path is None:
        raise FixDetectionFailed(f"{binary} not on PATH; install postfix/dovecot or fix PATH")
    return path


def _run(argv: list[str]) -> str:
    """Run a read-only detection subprocess; raise FixDetectionFailed on non-zero."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    except OSError as e:
        raise FixDetectionFailed(f"exec {argv[0]} failed: {e}") from e
    if proc.returncode != 0:
        raise FixDetectionFailed(
            f"{argv[0]} exit {proc.returncode}: {proc.stderr.strip() or '(no stderr)'}"
        )
    return proc.stdout


def _postconf_n() -> dict[str, str]:
    """Parse `postconf -n` output. Returns {key: value} for non-default params."""
    out = _run([_which_or_raise("postconf"), "-n"])
    result: dict[str, str] = {}
    for line in out.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, _, val = line.partition("=")
        result[key.strip()] = val.strip()
    return result


def _postconf_d(key: str) -> str:
    """Read postfix default-value for a single key. Strips `<key> = ` prefix."""
    out = _run([_which_or_raise("postconf"), "-d", key]).strip()
    _, _, val = out.partition("=")
    return val.strip()


def _doveconf_n() -> str:
    """Raw `doveconf -n` text. Block-scoped, so we keep it as text + parse on demand."""
    return _run([_which_or_raise("doveconf"), "-n"])


def _doveconf_h(key: str) -> str:
    """`doveconf -h <key>` returns just the value, no key prefix."""
    return _run([_which_or_raise("doveconf"), "-h", key]).strip()


_MLMMJ_SVC_RE = re.compile(r"^(mlmmj-[a-z]+)\s+unix\b", re.MULTILINE)
_PASSDB_SQL_RE = re.compile(r"passdb\s*\{[^}]*driver\s*=\s*sql", re.DOTALL)
_USERDB_SQL_RE = re.compile(r"userdb\s*\{[^}]*driver\s*=\s*sql", re.DOTALL)
_LMTP_LISTENER_RE = re.compile(r"unix_listener\s+private/dovecot-lmtp")


def _dovecot_etc_dir(postfix_config_dir: str) -> str:
    """Derive dovecot etc dir from postfix config_dir prefix.

    OS convention pairs postfix and dovecot in the same etc tree:
    /etc/postfix ↔ /etc/dovecot (Debian/Ubuntu/RHEL),
    /usr/local/etc/postfix ↔ /usr/local/etc/dovecot (FreeBSD pkg).
    """
    if postfix_config_dir.startswith("/usr/local/etc/"):
        return "/usr/local/etc/dovecot"
    return "/etc/dovecot"


def _doveconf_safe(key: str) -> str:
    """`doveconf -h <key>` but never raise on missing key — return '' instead.

    `mail_uid` / `mail_gid` are often unset (= use first_valid_uid). Treat
    a non-zero exit as 'unset' rather than detection failure.
    """
    try:
        return _doveconf_h(key)
    except FixDetectionFailed:
        return ""


def detect() -> dict[str, str]:
    """Probe live postfix + dovecot. Returns flat dict; see plan doc for key list."""
    pc_n = _postconf_n()
    config_dir = _postconf_d("config_directory")
    mf_raw = _run([_which_or_raise("postconf"), "-Mf"])
    mlmmj_services = ",".join(_MLMMJ_SVC_RE.findall(mf_raw))

    dc_n = _doveconf_n()
    base_dir = _doveconf_h("base_dir")
    etc_dir = _dovecot_etc_dir(config_dir)

    base = pc_n.get("virtual_mailbox_base", "")
    fs_base_uid = ""
    fs_base_gid = ""
    if base:
        try:
            st = os.stat(base)
            fs_base_uid = str(st.st_uid)
            fs_base_gid = str(st.st_gid)
        except OSError:
            pass

    return {
        "postfix.config_dir": config_dir,
        "dovecot.base_dir": base_dir,
        "dovecot.etc_dir": etc_dir,
        "virtual_mailbox_base": base,
        "virtual_mailbox_maps": pc_n.get("virtual_mailbox_maps", ""),
        "virtual_alias_maps": pc_n.get("virtual_alias_maps", ""),
        "virtual_mailbox_domains": pc_n.get("virtual_mailbox_domains", ""),
        "transport_maps": pc_n.get("transport_maps", ""),
        "virtual_transport": pc_n.get("virtual_transport", ""),
        "recipient_delimiter": pc_n.get("recipient_delimiter", ""),
        "mlmmj_services": mlmmj_services,
        "dovecot.mail_uid": _doveconf_safe("mail_uid"),
        "dovecot.mail_gid": _doveconf_safe("mail_gid"),
        "dovecot.first_valid_uid": _doveconf_safe("first_valid_uid"),
        "dovecot.has_sql_passdb": "true" if _PASSDB_SQL_RE.search(dc_n) else "false",
        "dovecot.has_sql_userdb": "true" if _USERDB_SQL_RE.search(dc_n) else "false",
        "dovecot.has_lmtp_listener": "true" if _LMTP_LISTENER_RE.search(dc_n) else "false",
        "fs.base_uid": fs_base_uid,
        "fs.base_gid": fs_base_gid,
    }


def _resolve_one(
    name: str,
    *,
    cli_override: int | None,
    mail: str,
    first_valid: str,
    fs_owner: str,
) -> int:
    """Highest-priority non-empty candidate wins; if two non-CLI candidates
    disagree, refuse."""
    if cli_override is not None:
        return cli_override
    candidates = [c for c in (mail, first_valid, fs_owner) if c]
    if not candidates:
        raise FixAmbiguity(
            f"cannot resolve effective vmail {name}: all candidates empty "
            f"(dovecot, fs owner); pass --vmail-{name}"
        )
    distinct = set(candidates)
    if len(distinct) > 1:
        raise FixAmbiguity(
            f"vmail {name} candidates disagree: {sorted(distinct)} "
            f"(dovecot={mail!r}, first_valid={first_valid!r}, fs={fs_owner!r}); "
            f"pass --vmail-{name} to force a value"
        )
    return int(candidates[0])


def effective_vmail(
    detected: dict[str, str],
    *,
    override_uid: int | None,
    override_gid: int | None,
) -> tuple[int, int]:
    """Resolve vmail uid+gid per spec priority: CLI > dovecot.mail_uid >
    first_valid_uid > fs owner. Refuse if any two non-override candidates
    disagree."""
    uid = _resolve_one(
        "uid",
        cli_override=override_uid,
        mail=detected.get("dovecot.mail_uid", ""),
        first_valid=detected.get("dovecot.first_valid_uid", ""),
        fs_owner=detected.get("fs.base_uid", ""),
    )
    gid = _resolve_one(
        "gid",
        cli_override=override_gid,
        mail=detected.get("dovecot.mail_gid", ""),
        first_valid="",  # dovecot has no first_valid_gid
        fs_owner=detected.get("fs.base_gid", ""),
    )
    return uid, gid


_MLMMJ_FOUR = ("mlmmj-receive", "mlmmj-bounce", "mlmmj-sub", "mlmmj-unsub")

# Postino-owned postfix main.cf params. These get reconciled.
# NOTE: virtual_mailbox_base is intentionally excluded — postino *consumes* it
# (via fs.base_uid fallback and --virtual-mailbox-base override) but does NOT own it.
# Including it would cause `postconf -X virtual_mailbox_base` to be emitted on every
# diff, nuking a valid operator-set value.
_POSTFIX_OWNED_KEYS = (
    "virtual_mailbox_maps",
    "virtual_alias_maps",
    "virtual_mailbox_domains",
    "transport_maps",
    "virtual_transport",
    "recipient_delimiter",
)


def _refusals(detected: dict[str, str], mlmmj_target_on: bool) -> list[str]:
    out: list[str] = []
    found = [s for s in detected.get("mlmmj_services", "").split(",") if s]
    if found and len(found) != 4:
        out.append(
            f"partial mlmmj: master.cf has {found} but not all 4 "
            f"{list(_MLMMJ_FOUR)}; fix master.cf by hand"
        )
    # When mlmmj_target_on=True and no services found, diff() handles the ADD path;
    # that is a valid apply step, not a refusal, so _refusals stays silent.
    if detected.get("dovecot.has_sql_passdb") == "true":
        out.append("dovecot already has passdb { driver = sql } — refusing to overlap")
    if detected.get("dovecot.has_sql_userdb") == "true":
        out.append("dovecot already has userdb { driver = sql } — refusing to overlap")
    if detected.get("dovecot.has_lmtp_listener") == "true":
        out.append(
            "dovecot already has service lmtp { unix_listener private/dovecot-lmtp } "
            "— refusing to overlap"
        )
    return out


def diff(
    detected: dict[str, str],
    target_postfix: dict[str, str],
    *,
    mlmmj_target_on: bool,
) -> list[str]:
    """Return human-readable diff lines (and copy-paste commands)."""
    lines: list[str] = []
    lines.append("─── postfix main.cf ───")

    for key in _POSTFIX_OWNED_KEYS:
        cur = detected.get(key, "")
        tgt = target_postfix.get(key, "")
        if cur == tgt:
            continue
        lines.append(f"- {key}: {cur or '(unset)'}")
        lines.append(f"+ {key}: {tgt or '(unset)'}")
        if tgt:
            lines.append(f"  $ postconf -e '{key}={tgt}'")
        else:
            lines.append(f"  $ postconf -X {key}")

    # master.cf mlmmj services
    detected_svcs = [s for s in detected.get("mlmmj_services", "").split(",") if s]
    if mlmmj_target_on:
        missing = [s for s in _MLMMJ_FOUR if s not in detected_svcs]
        if missing:
            lines.append("─── postfix master.cf ───")
            for s in missing:
                lines.append(f"+ {s}/unix → ADD")
                lines.append("  $ postino config gen --only master_cf --in-place")
    else:
        if detected_svcs:
            lines.append("─── postfix master.cf ───")
            for s in detected_svcs:
                lines.append(f"- {s}/unix → REMOVE")
                lines.append(f"  $ postconf -MX {s}/unix")

    refusals = _refusals(detected, mlmmj_target_on)
    if refusals:
        lines.append("─── refusals ───")
        for r in refusals:
            lines.append(f"! {r}")
    return lines


def build_target_postfix(
    *,
    postfix_dir: str,
    lmtp_socket: str,
    mlmmj_on: bool,
) -> dict[str, str]:
    """Canonical postino-owned main.cf params for the given mode."""
    tgt: dict[str, str] = {
        "virtual_mailbox_maps": f"mysql:{postfix_dir}/sql-virtual_mailbox_maps.cf",
        "virtual_alias_maps": f"mysql:{postfix_dir}/sql-virtual_alias_maps.cf",
        "virtual_mailbox_domains": f"mysql:{postfix_dir}/sql-virtual_domains.cf",
        "recipient_delimiter": "+",
    }
    if mlmmj_on:
        tgt["transport_maps"] = (
            f"mysql:{postfix_dir}/sql-routes.cf, mysql:{postfix_dir}/sql-virtual_transport_maps.cf"
        )
        tgt["virtual_transport"] = ""
    else:
        tgt["transport_maps"] = ""
        tgt["virtual_transport"] = f"lmtp:unix:{lmtp_socket}"
    return tgt


def _apply_postconf(*flags: str) -> None:
    try:
        _run([_which_or_raise("postconf"), *flags])
    except FixDetectionFailed as e:
        # Same subprocess plumbing; in the apply path a non-zero exit
        # is an apply failure, not a detection failure.
        raise FixApplyError(str(e)) from e


def postconf_set(key: str, value: str) -> None:
    """Idempotent `postconf -e key=value`. Raises FixApplyError on non-zero."""
    _apply_postconf("-e", f"{key}={value}")


def postconf_unset(key: str) -> None:
    """`postconf -X key` removes the key from main.cf."""
    _apply_postconf("-X", key)


def postconf_master_remove(service_slash_type: str) -> None:
    """`postconf -MX <service>/<type>` removes a master.cf service entry."""
    _apply_postconf("-MX", service_slash_type)


def postconf_master_set(service_slash_type: str, line: str) -> None:
    """`postconf -Me '<service>/<type>=<line>'` adds/edits a master.cf entry."""
    _apply_postconf("-Me", f"{service_slash_type}={line}")


def render_fragment(ctx: RenderContext) -> str:
    """Render dovecot-postino.conf body for the given context."""
    return _ENV.get_template("dovecot_postino.conf.j2").render(ctx=ctx)


def write_dovecot_fragment(target: Path, *, content: str) -> None:
    """Atomic write: write to .tmp sibling → os.rename → chmod.

    On any failure: try to remove the .tmp file; re-raise wrapped as FixApplyError.
    """
    tmp = target.with_name(f".{target.name}.tmp")
    try:
        tmp.write_text(content)
        os.rename(tmp, target)
        target.chmod(0o640)
    except OSError as e:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()
        raise FixApplyError(f"write {target} failed: {e}") from e
