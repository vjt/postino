"""Unit tests for ``postino.__main__._shuffle_globals``.

The shuffle moves a small allow-list of bool globals (``--json``,
``--quiet``, ``--no-color``) to the front of argv so Typer's root
callback can bind them regardless of where the operator wrote them on
the command line. Typer/Click bind options at declaration level; this
helper exists because there's no native opt-in for floating globals.

These tests exercise the helper in isolation. End-to-end position-flex
behaviour is verified separately in ``tests/cli/test_global_flag_position.py``.
"""

from __future__ import annotations

from postino.__main__ import (
    _FLOATING_GLOBALS,  # pyright: ignore[reportPrivateUsage]  # WHY: module-private allow-list exercised directly to assert shuffle contract.
    _shuffle_globals,  # pyright: ignore[reportPrivateUsage]  # WHY: module-private helper exercised directly to assert idempotence / order.
)


def test_shuffle_globals_idempotent() -> None:
    argv = ["--json", "domain", "list"]
    once = _shuffle_globals(argv, _FLOATING_GLOBALS)
    twice = _shuffle_globals(once, _FLOATING_GLOBALS)
    assert once == twice == ["--json", "domain", "list"]


def test_shuffle_globals_preserves_order_of_non_floats() -> None:
    argv = ["user", "add", "x@example.com", "--quota", "1G", "--json"]
    out = _shuffle_globals(argv, _FLOATING_GLOBALS)
    assert out == ["--json", "user", "add", "x@example.com", "--quota", "1G"]


def test_shuffle_globals_handles_multiple_floats() -> None:
    argv = ["user", "list", "--json", "--quiet"]
    assert _shuffle_globals(argv, _FLOATING_GLOBALS) == [
        "--json",
        "--quiet",
        "user",
        "list",
    ]
