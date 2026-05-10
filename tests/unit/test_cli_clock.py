"""The default clock built in `postino.cli` must be tz-aware UTC.

A naive `datetime.now()` would mix wall-clocks across hosts and produce
ambiguous `created`/`modified` columns; UTC is the lingua franca."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest


def test_cli_default_clock_is_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Callable[[], datetime]] = {}

    def fake_build_services(
        settings: Any,
        *,
        clock: Callable[[], datetime],
        echo: bool,
    ) -> Any:
        del settings, echo
        captured["clock"] = clock
        raise SystemExit(0)

    def fake_load_settings() -> Any:
        return object()

    from postino import cli

    monkeypatch.setattr(cli, "build_services", fake_build_services)
    monkeypatch.setattr(cli, "_load_settings", fake_load_settings)

    with pytest.raises(SystemExit):
        cli._entry(ctx=None, json=False)  # type: ignore[arg-type]  # WHY: ctx unused in this code path before SystemExit.

    clock = captured["clock"]
    now = clock()
    assert now.tzinfo is UTC
