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


def test_parse_all_privileges_global() -> None:
    rows = _parse_show_grants(_load("all_privileges_global.txt"))
    assert len(rows) == 1
    assert rows[0].scope == "global"
    assert rows[0].privs == frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})


def test_parse_all_privileges_on_db() -> None:
    rows = _parse_show_grants(_load("all_privileges_on_db.txt"))
    db_star_rows = [r for r in rows if r.scope == ("postfix", "*")]
    assert len(db_star_rows) == 1
    assert db_star_rows[0].privs == frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})


def test_parse_db_star_select_only() -> None:
    rows = _parse_show_grants(_load("db_star_select_only.txt"))
    db_star_rows = [r for r in rows if r.scope == ("postfix", "*")]
    assert len(db_star_rows) == 1
    assert db_star_rows[0].privs == frozenset({"SELECT"})


def test_parse_role_grant_is_ignored() -> None:
    rows = _parse_show_grants(_load("role_grant_ignored.txt"))
    # We get the mailbox row but not the role line.
    assert any(r.scope == ("postfix", "mailbox") for r in rows)
    assert all(r.scope != "global" or r.privs for r in rows)
    # Role-grant rows don't fit the canonical TO …@… shape, so the
    # parser skips them silently. Just confirm count.
    assert len(rows) == 1


def test_parse_proxy_grant_is_ignored() -> None:
    rows = _parse_show_grants(_load("proxy_grant_ignored.txt"))
    assert rows == []  # PROXY filtered out; USAGE has no data-path privs
