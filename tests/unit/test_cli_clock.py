"""The default clock built in `postino.cli` must be tz-aware UTC.

A naive `datetime.now()` would mix wall-clocks across hosts and produce
ambiguous `created`/`modified` columns; UTC is the lingua franca."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest


def test_cli_default_clock_is_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Callable[[], datetime]] = {}

    def fake_build_services(
        settings: Any,
        *,
        clock: Callable[[], datetime],
        echo: bool,
        **_kwargs: object,
    ) -> Any:
        del settings, echo
        captured["clock"] = clock
        raise SystemExit(0)

    def fake_load_settings() -> Any:
        return object()

    from postino import cli

    monkeypatch.setattr(cli, "build_services", fake_build_services)
    monkeypatch.setattr(cli, "_load_settings", fake_load_settings)

    # _entry now reads ctx.invoked_subcommand to decide whether to skip
    # build_services for the schema bootstrap (v0.10.2). Use a stub with
    # invoked_subcommand=None so the non-schema path runs and we hit the
    # patched build_services.
    fake_ctx = SimpleNamespace(invoked_subcommand=None)
    with pytest.raises(SystemExit):
        cli._entry(ctx=fake_ctx, json=False)  # type: ignore[arg-type]  # WHY: SimpleNamespace duck-types typer.Context for the two attributes _entry reads before SystemExit.

    clock = captured["clock"]
    now = clock()
    assert now.tzinfo is UTC
