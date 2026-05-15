"""Core types for config_gen.

GenInput   — operator-supplied generation parameters (CLI bridge)
RenderContext — enriched, frozen view passed to every render
RenderResult  — one emitted artifact (rel_path, content, mode)
GenResult     — return value of generate()
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, SecretStr

from postino_core.check.consistency import Finding
from postino_core.enums import IdentityBackend


class GenInput(BaseModel):
    """Operator-supplied parameters. Frozen + strict + extra=forbid."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    db_url: SecretStr
    identity_backend: IdentityBackend

    mlmmj_spool_dir: Path = Path("/var/spool/mlmmj")
    mlmmj_uid: str = "mlmmj"
    mlmmj_gid: str = "mlmmj"

    vmail_uid: int = 5000
    vmail_gid: int = 5000

    postfix_dir: Path = Path("/etc/postfix")
    dovecot_dir: Path = Path("/etc/dovecot")
    lmtp_socket: str = "private/dovecot-lmtp"

    in_place: bool = False
    skip_preflight: bool = False
    skip_postcheck: bool = False
    only: frozenset[str] = frozenset()
    skip: frozenset[str] = frozenset()


class RenderContext(BaseModel):
    """What every template sees. Built once by _build_context()."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    input: GenInput

    db_user: str
    db_password: SecretStr
    db_host: str
    db_port: int
    db_name: str

    has_alias_domains: bool
    has_routes_rows: bool
    schema_version: str


class RenderResult(BaseModel):
    """One emitted artifact."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    rel_path: Path  # relative to out_dir, e.g. Path("conf.d/auth-sql.conf.ext")
    content: str
    mode: int  # 0o640 for cred-bearing cfs, 0o644 for snippets


class GenResult(BaseModel):
    """Return value of generate()."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    written: list[RenderResult]
    preflight: list[Finding]
    postcheck: list[Finding]
    out_dir: Path
