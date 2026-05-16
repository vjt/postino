"""Template registry. One row per emitted artifact.

Every registered artifact is emitted on every generate(). Order in the
dict is emit order (Python dicts preserve insertion order). Operator
overrides via --only / --skip live on render_all().

Rationale: a DB-only change (adding a mailing-list row in `routes`, or
an alias_domain row) must not require regenerating postfix + dovecot
config + reloading services. generate() output is purely a function of
GenInput, not of current table contents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, NamedTuple

from jinja2 import Environment, PackageLoader, StrictUndefined

from postino_core.config_gen.input import RenderContext, RenderResult
from postino_core.errors import RenderError


class TemplateSpec(NamedTuple):
    """One row of the registry: what to emit, from which template, with what mode."""

    rel_path: Path
    template_name: str
    mode: int


_MLMMJ_GATED: Final[frozenset[str]] = frozenset({"master_cf", "sql_routes"})

# --- Registry: name → TemplateSpec.  Order = emit order. -------------------
_REGISTRY: Final[dict[str, TemplateSpec]] = {
    "master_cf": TemplateSpec(Path("master.cf"), "master.cf.j2", 0o644),
    "main_cf": TemplateSpec(Path("main.cf"), "main.cf.j2", 0o644),
    "sql_mailbox": TemplateSpec(
        Path("sql-virtual_mailbox_maps.cf"),
        "sql_virtual_mailbox_maps.cf.j2",
        0o640,
    ),
    "sql_alias": TemplateSpec(
        Path("sql-virtual_alias_maps.cf"),
        "sql_virtual_alias_maps.cf.j2",
        0o640,
    ),
    "sql_domains": TemplateSpec(
        Path("sql-virtual_domains.cf"),
        "sql_virtual_domains.cf.j2",
        0o640,
    ),
    "sql_alias_alias_domain": TemplateSpec(
        Path("sql-virtual_alias_alias_domain_maps.cf"),
        "sql_virtual_alias_alias_domain_maps.cf.j2",
        0o640,
    ),
    "sql_mailbox_alias_domain": TemplateSpec(
        Path("sql-virtual_mailbox_alias_domain_maps.cf"),
        "sql_virtual_mailbox_alias_domain_maps.cf.j2",
        0o640,
    ),
    "sql_transport": TemplateSpec(
        Path("sql-virtual_transport_maps.cf"),
        "sql_virtual_transport_maps.cf.j2",
        0o640,
    ),
    "sql_routes": TemplateSpec(
        Path("sql-routes.cf"),
        "sql_routes.cf.j2",
        0o640,
    ),
    "dovecot_sql": TemplateSpec(
        Path("dovecot-sql.conf.ext"),
        "dovecot_sql.conf.ext.j2",
        0o640,
    ),
    "dovecot_auth": TemplateSpec(
        Path("conf.d/auth-sql.conf.ext"),
        "dovecot_auth_sql.conf.ext.j2",
        0o640,
    ),
    "dovecot_lmtp": TemplateSpec(
        Path("conf.d/20-lmtp.conf"),
        "dovecot_20_lmtp.conf.j2",
        0o644,
    ),
}


def registry_names() -> frozenset[str]:
    """Public view of registered artifact names. For --only/--skip validation."""
    return frozenset(_REGISTRY.keys())


_ENV: Final = Environment(
    loader=PackageLoader("postino_core.config_gen", "templates"),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    autoescape=False,  # outputs are postfix/dovecot cfs, not HTML
)


def render_one(name: str, ctx: RenderContext) -> RenderResult:
    """Render a single template by registry name. Raises RenderError on Jinja failure."""
    spec = _REGISTRY[name]
    try:
        content = _ENV.get_template(spec.template_name).render(ctx=ctx)
    except Exception as e:
        raise RenderError(spec.template_name, e) from e
    return RenderResult(rel_path=spec.rel_path, content=content, mode=spec.mode)


def render_all(
    ctx: RenderContext,
    *,
    only: frozenset[str] = frozenset(),
    skip: frozenset[str] = frozenset(),
) -> list[RenderResult]:
    """Render every artifact, honouring operator --only / --skip overrides.

    Artifacts in _MLMMJ_GATED are silently skipped when mlmmj is off
    (settings.mlmmj_spool_dir is None) — the canonical no-mlmmj host
    has no master.cf hand-tweaks and routes its mail straight to lmtp
    via main.cf's virtual_transport.
    """
    mlmmj_on = ctx.input.mlmmj_spool_dir is not None
    results: list[RenderResult] = []
    for name in _REGISTRY:
        if name in skip:
            continue
        if only and name not in only:
            continue
        if name in _MLMMJ_GATED and not mlmmj_on:
            continue
        results.append(render_one(name, ctx))
    return results
