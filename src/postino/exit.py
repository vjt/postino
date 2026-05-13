"""CLI exit + state primitives.

Lives in a leaf module so every command can import ``exit_with_error``
eagerly (i.e. at module load) without circular-import dances back through
``postino.cli``. Also pins the typed shape of ``typer.Context.obj`` so
each command body has a single typed accessor instead of casting from
``dict[str, Any]``.
"""

from __future__ import annotations

import sys
from typing import NoReturn, TypedDict

import typer
from rich.console import Console

from postino_core.errors import (
    AlreadyExistsError,
    CapacityError,
    ConfigError,
    DBError,
    DeadlockError,
    FilesystemError,
    HookError,
    MailctlError,
    MlmmjError,
    NotFoundError,
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
    ConfigError: 4,
    DBError: 5,
    FilesystemError: 6,
    HookError: 7,
    DeadlockError: 8,
    MlmmjError: 9,
    RuleViolationError: 10,
}


def exit_with_error(err: MailctlError) -> NoReturn:
    """Print ``err`` to stderr and ``sys.exit`` with the documented code."""
    console = Console(stderr=True)
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
