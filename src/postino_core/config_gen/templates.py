"""Template registry. One row per emitted artifact.

To skip an artifact under runtime conditions (e.g. no alias_domain
rows), the row's skip_if predicate returns True. Order in the dict
is emit order (Python dicts preserve insertion order).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Final, NamedTuple

from jinja2 import Environment, PackageLoader, StrictUndefined

from postino_core.config_gen.input import RenderContext, RenderResult
from postino_core.errors import RenderError

SkipPredicate = Callable[[RenderContext], bool]


class TemplateSpec(NamedTuple):
    """One row of the registry: what to emit, from which template, with what mode."""

    rel_path: Path
    template_name: str
    mode: int
    skip_if: SkipPredicate


# --- Skip predicates --------------------------------------------------------
def _always(ctx: RenderContext) -> bool:
    return False


def _no_alias_domain(ctx: RenderContext) -> bool:
    return not ctx.has_alias_domains


def _no_routes(ctx: RenderContext) -> bool:
    return not ctx.has_routes_rows


# --- Registry: name → TemplateSpec.  Order = emit order. -------------------
_REGISTRY: Final[dict[str, TemplateSpec]] = {
    "master_cf": TemplateSpec(Path("master.cf"), "master.cf.j2", 0o644, _always),
    "main_cf": TemplateSpec(Path("main.cf"), "main.cf.j2", 0o644, _always),
    "sql_mailbox": TemplateSpec(
        Path("sql-virtual_mailbox_maps.cf"),
        "sql_virtual_mailbox_maps.cf.j2",
        0o640,
        _always,
    ),
    "sql_alias": TemplateSpec(
        Path("sql-virtual_alias_maps.cf"),
        "sql_virtual_alias_maps.cf.j2",
        0o640,
        _always,
    ),
    "sql_domains": TemplateSpec(
        Path("sql-virtual_domains.cf"),
        "sql_virtual_domains.cf.j2",
        0o640,
        _always,
    ),
    "sql_alias_alias_domain": TemplateSpec(
        Path("sql-virtual_alias_alias_domain_maps.cf"),
        "sql_virtual_alias_alias_domain_maps.cf.j2",
        0o640,
        _no_alias_domain,
    ),
    "sql_mailbox_alias_domain": TemplateSpec(
        Path("sql-virtual_mailbox_alias_domain_maps.cf"),
        "sql_virtual_mailbox_alias_domain_maps.cf.j2",
        0o640,
        _no_alias_domain,
    ),
    "sql_transport": TemplateSpec(
        Path("sql-virtual_transport_maps.cf"),
        "sql_virtual_transport_maps.cf.j2",
        0o640,
        _always,
    ),
    "sql_routes": TemplateSpec(
        Path("sql-routes.cf"),
        "sql_routes.cf.j2",
        0o640,
        _no_routes,
    ),
    "dovecot_sql": TemplateSpec(
        Path("dovecot-sql.conf.ext"),
        "dovecot_sql.conf.ext.j2",
        0o640,
        _always,
    ),
    "dovecot_auth": TemplateSpec(
        Path("conf.d/auth-sql.conf.ext"),
        "dovecot_auth_sql.conf.ext.j2",
        0o640,
        _always,
    ),
    "dovecot_lmtp": TemplateSpec(
        Path("conf.d/20-lmtp.conf"),
        "dovecot_20_lmtp.conf.j2",
        0o644,
        _always,
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
    """Render every artifact whose skip_if(ctx) is False and is not in --skip."""
    results: list[RenderResult] = []
    for name, spec in _REGISTRY.items():
        if name in skip:
            continue
        if only and name not in only:
            continue
        if spec.skip_if(ctx):
            continue
        results.append(render_one(name, ctx))
    return results
