"""Pre-emit blocker checks. Refuses to generate if the DB is not in
a state postino can produce a coherent config set from.
"""

from __future__ import annotations

from typing import Final

from packaging.version import InvalidVersion, Version
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from postino_core.check.types import Finding
from postino_core.config_gen.input import GenInput

REQUIRED_MIN_SCHEMA: Final[Version] = Version("0.12.0")
REQUIRED_MIN_SCHEMA_DISPLAY: Final[str] = "v0.12.0"


def _err(name: str, message: str) -> Finding:
    return Finding(name=name, severity="error", message=message)


def _warn(name: str, message: str) -> Finding:
    return Finding(name=name, severity="warn", message=message)


def _ok(name: str, message: str) -> Finding:
    return Finding(name=name, severity="info", message=message)


def _parse(raw: str) -> Version | None:
    try:
        return Version(raw.lstrip("v"))
    except InvalidVersion:
        return None


def run(input: GenInput) -> list[Finding]:
    """Run all preflight checks. Returns Findings; caller decides on errors."""
    findings: list[Finding] = []
    try:
        engine = create_engine(input.db_url.get_secret_value(), future=True)
        with engine.connect() as conn:
            insp = inspect(conn)
            if not insp.has_table("postino_schema_version"):
                findings.append(
                    _err(
                        "schema_version_table",
                        "postino_schema_version table missing; run `postino schema migrate` first",
                    )
                )
                return findings

            raw_version = conn.execute(
                text("SELECT version FROM postino_schema_version")
            ).scalar_one_or_none()
            if raw_version is None:
                findings.append(
                    _err(
                        "schema_version_empty",
                        "postino_schema_version table has no row; "
                        "run `postino schema migrate` to populate",
                    )
                )
                return findings
            parsed = _parse(str(raw_version))
            if parsed is None:
                findings.append(
                    _err(
                        "schema_version_invalid",
                        f"postino_schema_version.version={raw_version!r} is not valid semver",
                    )
                )
                return findings
            if parsed < REQUIRED_MIN_SCHEMA:
                findings.append(
                    _err(
                        "schema_version",
                        f"schema version {raw_version} < required "
                        f"{REQUIRED_MIN_SCHEMA_DISPLAY}; "
                        f"run `postino schema migrate`",
                    )
                )
                return findings
            findings.append(_ok("schema_version", f"version={raw_version}"))

            # Skip-guard: alias_domain has rows but operator passed --skip
            n_ad = conn.execute(text("SELECT COUNT(*) FROM alias_domain")).scalar_one()
            skip_alias = {"sql_alias_alias_domain", "sql_mailbox_alias_domain"}
            if n_ad > 0 and skip_alias.intersection(input.skip):
                findings.append(
                    _err(
                        "alias_domain_skipped",
                        f"alias_domain has {n_ad} rows but --skip excludes "
                        f"alias_domain renderers; would emit broken set",
                    )
                )

            # WARN-level facts (don't refuse, but flag)
            n_routes = conn.execute(text("SELECT COUNT(*) FROM routes")).scalar_one()
            if n_routes == 0:
                findings.append(_warn("routes_empty", "routes table empty (no mlmmj lists)"))
            n_mb = conn.execute(text("SELECT COUNT(*) FROM mailbox")).scalar_one()
            if n_mb == 0:
                findings.append(_warn("mailbox_empty", "mailbox table empty"))
    except SQLAlchemyError as e:
        findings.append(_err("db_connect", f"cannot connect to DB: {e}"))
    return findings
