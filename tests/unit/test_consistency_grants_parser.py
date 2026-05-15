"""Unit tests for _parse_show_grants — pure-function parser over the
canonical SHOW GRANTS row format MySQL/MariaDB emits."""

from __future__ import annotations

from pathlib import Path

from postino_core.check.consistency import (
    GrantRow,
    _parse_show_grants,  # pyright: ignore[reportPrivateUsage]  # WHY: testing private parser symbol from inside the package.
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "grants"


def _load(name: str) -> list[str]:
    return FIXTURES.joinpath(name).read_text().strip().splitlines()


def test_parse_exact_required() -> None:
    rows = _parse_show_grants(_load("exact_required.txt"))
    # USAGE row is parsed but emits an empty priv set (we filter USAGE out).
    # Six data rows each with the canonical priv subset on (postfix, <table>).
    mailbox_row = next(r for r in rows if r.scope == ("postfix", "mailbox"))
    assert mailbox_row.privs == frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})
    log_row = next(r for r in rows if r.scope == ("postfix", "log"))
    assert log_row.privs == frozenset({"SELECT", "INSERT"})


def test_grant_row_named_tuple_shape() -> None:
    row = GrantRow(scope="global", privs=frozenset({"SELECT"}))
    assert row.scope == "global"
    assert row.privs == frozenset({"SELECT"})
