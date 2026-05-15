"""CLI exit + state primitives.

Lives in a leaf module so every command can import ``exit_with_error``
eagerly (i.e. at module load) without circular-import dances back through
``postino.cli``. Also pins the typed shape of ``typer.Context.obj`` so
each command body has a single typed accessor instead of casting from
``dict[str, Any]``.
"""

from __future__ import annotations

import os
import sys
from typing import NoReturn, TypedDict

import typer
from rich.console import Console

from postino_core.errors import (
    AlreadyExistsError,
    CapacityError,
    CollisionRefused,
    ConfigError,
    DBError,
    DeadlockError,
    FilesystemError,
    HookError,
    MailctlError,
    MlmmjError,
    NotFoundError,
    PostCheckFailed,
    PreflightFailed,
    RenderError,
    RuleViolationError,
)
from postino_core.services.bundle import ServicesBundle


class CliState(TypedDict):
    """Shape of ``typer.Context.obj`` after ``postino.cli._entry``."""

    services: ServicesBundle
    json: bool
    quiet: bool
    no_color: bool


_EXIT_CODES: dict[type[MailctlError], int] = {
    NotFoundError: 1,
    AlreadyExistsError: 2,
    CapacityError: 3,
    # config_gen-specific subclasses of ConfigError must precede ConfigError
    # itself; exit_with_error returns the first isinstance match.
    PreflightFailed: 11,
    CollisionRefused: 12,
    RenderError: 13,
    PostCheckFailed: 14,
    ConfigError: 4,
    DBError: 5,
    FilesystemError: 6,
    HookError: 7,
    DeadlockError: 8,
    MlmmjError: 9,
    RuleViolationError: 10,
}


def _env_no_color() -> bool:
    """Whether NO_COLOR or CI env disables color.

    Used by ``exit_with_error`` because it may run before ``ctx.obj`` is
    initialised (e.g. if ``_load_settings()`` raises during the root
    callback) — so reading from ``CliState`` is not safe. The two env
    vars are the documented escape hatch (no-color.org + CI convention);
    users who want guaranteed monochrome error output set ``NO_COLOR=1``
    in their environment and it works everywhere, including this path.
    """
    return bool(os.environ.get("NO_COLOR")) or os.environ.get("CI", "").lower() == "true"


def exit_with_error(err: MailctlError) -> NoReturn:
    """Print ``err`` to stderr and ``sys.exit`` with the documented code.

    Honors ``NO_COLOR`` / ``CI`` env vars for color suppression. The
    ``--no-color`` flag itself is NOT consulted here because this
    function is called from contexts where ``ctx.obj`` may not exist
    (root-callback exception handler) — see ``_env_no_color`` docstring.
    """
    no_color = _env_no_color()
    console = Console(
        stderr=True,
        color_system=None if no_color else "auto",
        no_color=no_color,
    )
    console.print(f"[red]error:[/red] {err}")
    code = next(
        (c for cls, c in _EXIT_CODES.items() if isinstance(err, cls)),
        99,
    )
    sys.exit(code)


def get_state(ctx: typer.Context) -> CliState:
    """Typed accessor for ``typer.Context.obj`` shaped as ``CliState``."""
    obj = ctx.obj
    assert isinstance(obj, dict), "ctx.obj not initialised (callback bypassed?)"
    return obj  # type: ignore[return-value]  # WHY: dict shape matches CliState; runtime check above guards the cast.


def get_services(ctx: typer.Context) -> ServicesBundle:
    """Typed accessor for the services bundle in ``ctx.obj``."""
    return get_state(ctx)["services"]


def is_json(ctx: typer.Context) -> bool:
    """Whether ``--json`` was passed."""
    return get_state(ctx)["json"]


def is_quiet(ctx: typer.Context) -> bool:
    """Whether ``--quiet`` was passed."""
    return get_state(ctx)["quiet"]


def is_no_color(ctx: typer.Context) -> bool:
    """Whether color output is disabled (via ``--no-color`` or NO_COLOR/CI env)."""
    return get_state(ctx)["no_color"]
