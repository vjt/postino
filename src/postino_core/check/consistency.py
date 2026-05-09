"""postino check — consistency validator (read-only)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict
from sqlalchemy import MetaData, text
from sqlalchemy.engine import Engine

from postino_core.config import PostinoSettings


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    name: str
    ok: bool
    message: str


@dataclass(frozen=True)
class CheckResult:
    findings: list[Finding]

    @property
    def ok(self) -> bool:
        return all(f.ok for f in self.findings)


def run_consistency_check(
    *,
    settings: PostinoSettings,
    engine: Engine,
    metadata: MetaData,
) -> CheckResult:
    findings: list[Finding] = []
    findings.append(_check_db_reachable(engine))
    findings.append(_check_required_tables(metadata))
    findings.append(_check_mailbox_base(settings))
    findings.append(_check_postcreation_hook(settings))
    findings.append(_check_postfix_sql_credentials_match(settings))
    return CheckResult(findings=findings)


def _check_db_reachable(engine: Engine) -> Finding:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001 — top-level
        return Finding(name="db_reachable", ok=False, message=f"DB unreachable: {e}")
    return Finding(name="db_reachable", ok=True, message="DB reachable")


def _check_required_tables(md: MetaData) -> Finding:
    required = {"mailbox", "alias", "domain", "quota2", "log"}
    missing = required - set(md.tables.keys())
    if missing:
        return Finding(
            name="schema_tables", ok=False,
            message=f"missing tables: {sorted(missing)}",
        )
    return Finding(name="schema_tables", ok=True, message="all required tables present")


def _check_mailbox_base(s: PostinoSettings) -> Finding:
    p = s.virtual_mailbox_base
    if not p.is_dir():
        return Finding(
            name="mailbox_base", ok=False,
            message=f"virtual_mailbox_base does not exist or is not a directory: {p}",
        )
    return Finding(name="mailbox_base", ok=True, message=f"{p} exists")


def _check_postcreation_hook(s: PostinoSettings) -> Finding:
    h = s.postcreation_hook
    if not h.exists():
        return Finding(
            name="postcreation_hook", ok=False,
            message=f"postcreation hook missing: {h}",
        )
    if not os.access(h, os.X_OK):
        return Finding(
            name="postcreation_hook", ok=False,
            message=f"postcreation hook not executable: {h}",
        )
    return Finding(name="postcreation_hook", ok=True, message=f"{h} executable")


def _check_postfix_sql_credentials_match(s: PostinoSettings) -> Finding:
    cf = s.postfix_sql_dir / "sql-virtual_mailbox_maps.cf"
    if not cf.exists():
        return Finding(
            name="postfix_sql_cf", ok=False,
            message=f"postfix sql cf missing: {cf}",
        )
    return Finding(name="postfix_sql_cf", ok=True, message=f"{cf} present")
