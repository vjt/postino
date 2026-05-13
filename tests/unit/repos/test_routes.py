"""Unit tests for RoutesRepository — pattern generation + repo CRUD.

Most tests bind to an SQLite-backed reflected schema fixture for speed;
the integration test suite covers MariaDB compatibility separately.
"""

from __future__ import annotations

import pytest

from postino_core.repos.routes import Route, _mlmmj_patterns  # pyright: ignore[reportPrivateUsage]  # WHY: module-private helper exercised directly to assert pattern-generation contract


def test_mlmmj_patterns_emits_five_rows() -> None:
    rows = _mlmmj_patterns("team@lists.example.org")
    assert len(rows) == 5
    patterns = {p for p, _t, _pri in rows}
    transports = {t for _p, t, _pri in rows}
    priorities = {pri for _p, _t, pri in rows}
    assert transports == {
        "mlmmj-bounce:",
        "mlmmj-sub:",
        "mlmmj-unsub:",
        "mlmmj-help:",
        "mlmmj-receive:",
    }
    assert priorities == {10, 50}  # 4× priority 10 + 1× priority 50
    # localpart-anchored, not domain-wide
    assert any(p == r"^team-bounces@lists\.example\.org$" for p in patterns)
    assert any(p == r"^team-confirm-sub-.+@lists\.example\.org$" for p in patterns)
    assert any(p == r"^team-confirm-unsub-.+@lists\.example\.org$" for p in patterns)
    assert any(p == r"^team-help@lists\.example\.org$" for p in patterns)
    # catchall absorbs +ext for plus-addressing
    assert any(p == r"^team(\+.+)?@lists\.example\.org$" for p in patterns)


def test_mlmmj_patterns_escapes_regex_metacharacters_in_address() -> None:
    # local-part `+` is invalid for a list name but other metacharacters
    # like `.` must be escaped. Real-world example: `team.b@lists.example.org`.
    rows = _mlmmj_patterns("team.b@lists.example.org")
    patterns = {p for p, _t, _pri in rows}
    # the literal `.` in the localpart must be `\.` in the regex
    assert r"^team\.b-bounces@lists\.example\.org$" in patterns
    assert r"^team\.b(\+.+)?@lists\.example\.org$" in patterns


def test_mlmmj_patterns_rejects_invalid_address() -> None:
    with pytest.raises(ValueError):
        _mlmmj_patterns("no-at-sign")
    with pytest.raises(ValueError):
        _mlmmj_patterns("@no-localpart.example.org")
    with pytest.raises(ValueError):
        _mlmmj_patterns("localpart@")


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
