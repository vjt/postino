"""Unit tests for RoutesRepository — pattern generation + repo CRUD.

Most tests bind to an SQLite-backed reflected schema fixture for speed;
the integration test suite covers MariaDB compatibility separately.
"""

from __future__ import annotations

import pytest

from postino_core.repos.routes import Route


def test_route_model_frozen_strict() -> None:
    r = Route(
        pattern=r"^team@lists\.example\.org$",
        transport="mlmmj-receive:",
        domain="lists.example.org",
        list_address="team@lists.example.org",
        priority=50,
        active=True,
    )
    assert r.pattern == r"^team@lists\.example\.org$"
    assert r.transport == "mlmmj-receive:"
    with pytest.raises(Exception):
        r.priority = 10  # type: ignore[misc]  # WHY: testing frozen model rejection at runtime
