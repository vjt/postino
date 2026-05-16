"""postino config fix — reconcile a live postfix+dovecot deployment to canonical shape.

Detection: shell out to postconf/doveconf, parse output.
Diff: compare detected dict to canonical target dict, emit human-readable lines + refusals.
Apply: run postconf -e/-X/-Me/-MX, atomic-rename a dovecot fragment file.

No new Pydantic models — detection returns a flat dict[str, str]; refusals
are surfaced as typed exceptions; the renderer (config_gen.generate) owns
the sql cf writes.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from postino_core.errors import FixDetectionFailed


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

_DOVECOT_ETC_BY_BASE: dict[str, str] = {
    "/var/run/dovecot": "/etc/dovecot",
    "/run/dovecot": "/etc/dovecot",
    "/var/spool/dovecot": "/usr/local/etc/dovecot",  # FreeBSD pkg layout
    "/var/run/dovecot/run": "/usr/local/etc/dovecot",
}


def _dovecot_etc_dir(base_dir: str) -> str:
    """Map dovecot base_dir to its etc/conf.d location. Fallback to /etc/dovecot."""
    return _DOVECOT_ETC_BY_BASE.get(base_dir.rstrip("/"), "/etc/dovecot")


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
    etc_dir = _dovecot_etc_dir(base_dir)

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
