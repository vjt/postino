"""generate() — preflight -> render-to-staging -> atomic rename -> parse-check.

Atomicity: render into a sibling directory of out_dir (same filesystem),
then `os.rename(staging, out_dir)` commits in a single POSIX-atomic step.
For --in-place, the existing out_dir is first renamed to a backup
sibling; the backup is removed on commit success, or renamed back on
commit failure.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url

from postino_core.check.types import Finding
from postino_core.config import parse_postfix_sql_cf
from postino_core.config_gen.input import (
    GenInput,
    GenResult,
    RenderContext,
    RenderResult,
)
from postino_core.config_gen.preflight import run as run_preflight
from postino_core.config_gen.templates import render_all
from postino_core.errors import (
    CollisionRefused,
    ConfigError,
    PostCheckFailed,
    PreflightFailed,
)


def _build_context(input: GenInput) -> RenderContext:
    url = make_url(input.db_url.get_secret_value())
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            version_raw = conn.execute(
                text("SELECT version FROM postino_schema_version")
            ).scalar_one_or_none()
            version = "unknown" if version_raw is None else str(version_raw)
    finally:
        engine.dispose()
    return RenderContext(
        input=input,
        db_user=url.username or "",
        db_password=SecretStr(url.password or ""),
        db_host=url.host or "",
        db_port=url.port or 3306,
        db_name=url.database or "",
        schema_version=version,
    )


def _check_collision(out_dir: Path, in_place: bool, would_write: list[Path]) -> None:
    if in_place:
        return
    colliding = [str(p) for p in would_write if (out_dir / p).exists()]
    if colliding:
        raise CollisionRefused(colliding)


def _parse_check(out_dir: Path, written: list[RenderResult]) -> list[Finding]:
    """Re-parse every emitted sql-*.cf and assert required fields non-empty.

    PostfixSqlCredentials fields: host (singular), user, password (SecretStr),
    dbname. parse_postfix_sql_cf does not surface `query`; query-line content
    is validated by per-renderer goldens (Task 5 for master.cf).
    """
    findings: list[Finding] = []
    for res in written:
        name = res.rel_path.name
        if not name.startswith("sql-") or not name.endswith(".cf"):
            continue
        path = out_dir / res.rel_path
        try:
            cfg = parse_postfix_sql_cf(path)
        except ConfigError as e:
            findings.append(
                Finding(
                    name=f"parse:{name}",
                    severity="error",
                    message=f"failed to parse emitted cf: {e}",
                )
            )
            continue
        pw = cfg.password.get_secret_value()
        for field, value in (
            ("host", cfg.host),
            ("user", cfg.user),
            ("password", pw),
            ("dbname", cfg.dbname),
        ):
            if not value:
                findings.append(
                    Finding(
                        name=f"empty:{name}:{field}",
                        severity="error",
                        message=f"{name} emitted with empty {field}",
                    )
                )
    return findings


def generate(input: GenInput, out_dir: Path) -> GenResult:
    """Render every artifact atomically. See spec section "Generation flow"."""
    ctx = _build_context(input)

    findings: list[Finding] = []
    if not input.skip_preflight:
        findings = run_preflight(input)
        if any(f.severity == "error" for f in findings):
            raise PreflightFailed(findings)

    staging = out_dir.parent / f".{out_dir.name}.postino-gen.tmp"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    try:
        results = render_all(ctx, only=input.only, skip=input.skip)
        for res in results:
            target = staging / res.rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(res.content)
            target.chmod(res.mode)

        _check_collision(out_dir, input.in_place, [r.rel_path for r in results])

        backup: Path | None = None
        if out_dir.exists() and input.in_place:
            backup = out_dir.parent / f"{out_dir.name}.postino-gen.bak"
            if backup.exists():
                shutil.rmtree(backup)
            os.rename(out_dir, backup)
        try:
            os.rename(staging, out_dir)
        except OSError:
            if backup is not None and not out_dir.exists():
                os.rename(backup, out_dir)  # roll back
            raise
        if backup is not None:
            shutil.rmtree(backup)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise

    postcheck: list[Finding] = []
    if not input.skip_postcheck:
        postcheck = _parse_check(out_dir, results)
        if any(f.severity == "error" for f in postcheck):
            raise PostCheckFailed(postcheck, out_dir)

    return GenResult(
        written=results,
        preflight=findings,
        postcheck=postcheck,
        out_dir=out_dir,
    )
