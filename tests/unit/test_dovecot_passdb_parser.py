"""Unit tests for the dovecot passdb extractor.

Covers the parser shape: comments stripped, brace depth tracked,
multiple passdb blocks captured, no false-positives from `userdb`
neighbours.
"""

from __future__ import annotations

from postino_core.check.consistency import (
    _extract_passdb_drivers,  # pyright: ignore[reportPrivateUsage]  # WHY: regression guard for the dovecot passdb chain parser; module-private by design.
)


def test_single_passdb_sql() -> None:
    text = """
    passdb {
      driver = sql
      args = /etc/dovecot/dovecot-sql.conf.ext
    }
    """
    assert _extract_passdb_drivers(text) == ["sql"]


def test_multiple_passdbs_returned_in_order() -> None:
    text = """
    passdb {
      driver = sql
    }

    passdb {
      driver = passwd-file
      args = scheme=PLAIN /etc/dovecot/users
    }
    """
    assert _extract_passdb_drivers(text) == ["sql", "passwd-file"]


def test_comments_are_ignored() -> None:
    text = """
    # passdb {
    #   driver = sql
    # }
    passdb {
      driver = ldap  # falls through to ldap
    }
    """
    assert _extract_passdb_drivers(text) == ["ldap"]


def test_userdb_blocks_are_not_picked_up() -> None:
    """`userdb` looks like `passdb` to a sloppy scanner — must not match."""
    text = """
    userdb {
      driver = sql
    }
    passdb {
      driver = pam
    }
    """
    assert _extract_passdb_drivers(text) == ["pam"]


def test_nested_braces_are_balanced() -> None:
    """Nested `{ ... }` inside passdb must not terminate the block early."""
    text = """
    passdb {
      driver = checkpassword
      args = {
        executable = /usr/local/bin/checkpw
      }
    }
    passdb {
      driver = static
    }
    """
    assert _extract_passdb_drivers(text) == ["checkpassword", "static"]


def test_empty_input_returns_empty() -> None:
    assert _extract_passdb_drivers("") == []
    assert _extract_passdb_drivers("# nothing here\nuserdb { driver = passwd }\n") == []
