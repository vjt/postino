"""Subprocess-driven e2e CLI tests for postino — write (mutating) commands.

Each test uses the function-scoped ``e2e_write_env`` fixture, which
TRUNCATEs + re-seeds the DB so mutations don't interfere across tests.
Kept in a separate file from the read-only suite so the module-scoped
``e2e_db`` seed for ``test_cli_e2e.py`` is not truncated mid-run.

Bug 1 regression: ``postino user add`` failed in production (m42) with
  "error: postcreation hook exit 1: stderr=''"
because HookRunner passed only USERNAME; the PA-style hook expected
USERNAME DOMAIN MAILDIR QUOTA and exited 1 when DOMAIN was empty.
The ``test_postino_user_add`` test below exercises the fixed four-arg
hook contract end-to-end.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from tests.e2e_cli.conftest import WriteEnv


def _json_list(text: str) -> list[dict[str, object]]:
    """Parse JSON output from postino --json into a typed list of dicts.

    Strips leading non-JSON content (e.g. password prompts on stdout)
    by scanning for the first '['.
    """
    start = text.find("[")
    assert start != -1, f"No JSON array found in output: {text!r}"
    data = json.loads(text[start:])
    assert isinstance(data, list)
    return data  # type: ignore[return-value]  # WHY: json.loads returns Any; the isinstance assert above guarantees list; element type dict[str, object] is a safe widening for test assertions.


def _json_dict(text: str) -> dict[str, object]:
    """Parse JSON output from postino --json into a typed dict.

    Strips leading non-JSON content (e.g. password prompts that land on
    stdout when stdin is a pipe) by scanning for the first '{'.
    """
    start = text.find("{")
    assert start != -1, f"No JSON object found in output: {text!r}"
    data = json.loads(text[start:])
    assert isinstance(data, dict)
    return data  # type: ignore[return-value]  # WHY: json.loads returns Any; the isinstance assert above guarantees dict; value type object is a safe widening for test assertions.


pytestmark = pytest.mark.integration  # requires POSTINO_TEST_DB_URL

_TIMEOUT = 30  # seconds

_POSTINO_BIN = Path(sys.executable).parent / "postino"


def _run(
    args: Sequence[str],
    env: dict[str, str],
    *,
    input: str | None = None,
) -> tuple[int, str, str]:
    """Run the installed ``postino`` binary; return (exit_code, stdout, stderr)."""
    import subprocess

    result = subprocess.run(
        [str(_POSTINO_BIN), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=_TIMEOUT,
        input=input,
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# domain write commands
# ---------------------------------------------------------------------------


def test_postino_domain_add(e2e_write_env: WriteEnv) -> None:
    """domain add creates a new domain row."""
    code, out, err = _run(
        ["--json", "domain", "add", "new.example.net"],
        e2e_write_env.env,
    )
    assert code == 0, f"stderr: {err}\nstdout: {out}"
    data = _json_dict(out)
    assert data["domain"] == "new.example.net"


def test_postino_domain_add_duplicate_exits_2(e2e_write_env: WriteEnv) -> None:
    """domain add on an already-existing domain exits 2 (AlreadyExistsError)."""
    code, _out, _err = _run(
        ["domain", "add", "write.example.com"],
        e2e_write_env.env,
    )
    assert code == 2


def test_postino_domain_del_empty(e2e_write_env: WriteEnv) -> None:
    """domain del removes an empty domain with --yes (skips confirmation prompt)."""
    code, out, err = _run(
        ["domain", "del", "other.example.com", "--yes"],
        e2e_write_env.env,
    )
    assert code == 0, f"stderr: {err}\nstdout: {out}"
    # Verify it's gone
    code2, out2, _ = _run(["--json", "domain", "list"], e2e_write_env.env)
    assert code2 == 0
    data = _json_list(out2)
    assert not any(d["domain"] == "other.example.com" for d in data)


def test_postino_domain_del_nonempty_requires_force(e2e_write_env: WriteEnv) -> None:
    """domain del refuses a non-empty domain without --force (CapacityError → exit 3)."""
    code, _out, _err = _run(
        ["domain", "del", "write.example.com", "--yes"],
        e2e_write_env.env,
    )
    assert code == 3


def test_postino_domain_del_nonexistent_exits_1(e2e_write_env: WriteEnv) -> None:
    """domain del on an unknown domain exits 1 (NotFoundError)."""
    code, _out, _err = _run(
        ["domain", "del", "ghost.nowhere.example.com", "--yes"],
        e2e_write_env.env,
    )
    assert code == 1


# ---------------------------------------------------------------------------
# user write commands
# ---------------------------------------------------------------------------


def test_postino_user_add(e2e_write_env: WriteEnv) -> None:
    """user add creates mailbox; postcreation hook receives USERNAME DOMAIN MAILDIR QUOTA.

    This is the regression test for Bug 1: the hook used to receive only
    USERNAME and exit 1 because DOMAIN/MAILDIR were empty strings.
    With the fix, it receives all four PA-style positional arguments.
    """
    from sqlalchemy import select

    code, out, err = _run(
        ["--json", "user", "add", "newuser@write.example.com", "--quota", "500M"],
        e2e_write_env.env,
        input="testpassword\ntestpassword\n",
    )
    assert code == 0, f"stderr: {err}\nstdout: {out}"
    data = _json_dict(out)
    assert data["username"] == "newuser@write.example.com"

    # Verify DB row exists and is active
    mailbox = e2e_write_env.metadata.tables["mailbox"]
    with e2e_write_env.engine.connect() as conn:
        row = conn.execute(
            select(mailbox).where(mailbox.c.username == "newuser@write.example.com")
        ).fetchone()
    assert row is not None
    assert int(row._mapping["active"]) == 1  # type: ignore[index]  # WHY: SQLAlchemy RowMapping Any


def test_postino_user_add_unknown_domain_exits_1(e2e_write_env: WriteEnv) -> None:
    """user add for an unknown domain exits 1 (NotFoundError)."""
    code, _out, _err = _run(
        ["user", "add", "ghost@nowhere.example.net"],
        e2e_write_env.env,
        input="pw\npw\n",
    )
    assert code == 1


def test_postino_user_del(e2e_write_env: WriteEnv) -> None:
    """user del removes the mailbox row."""
    from sqlalchemy import select

    code, out, err = _run(
        ["user", "del", "existing@write.example.com", "--yes", "--remove-maildir"],
        e2e_write_env.env,
    )
    assert code == 0, f"stderr: {err}\nstdout: {out}"

    mailbox = e2e_write_env.metadata.tables["mailbox"]
    with e2e_write_env.engine.connect() as conn:
        row = conn.execute(
            select(mailbox).where(mailbox.c.username == "existing@write.example.com")
        ).fetchone()
    assert row is None


def test_postino_user_del_nonexistent_exits_1(e2e_write_env: WriteEnv) -> None:
    """user del on an unknown mailbox exits 1 (NotFoundError)."""
    code, _out, _err = _run(
        ["user", "del", "ghost@write.example.com", "--yes"],
        e2e_write_env.env,
    )
    assert code == 1


def test_postino_user_passwd(e2e_write_env: WriteEnv) -> None:
    """user passwd changes the password hash in the DB.

    The fixture seeds the row with the ``{NOAUTH}`` sentinel for
    economy (no real hash needed). Task 15's CLI guard requires
    ``--claim`` to rotate a sentinel row into SQL auth — exercise that
    path here. Mailboxes already holding a real hash exit-0 without
    ``--claim`` (covered by tests/unit/test_cli_user_passwd_claim.py)."""
    from sqlalchemy import select

    mailbox = e2e_write_env.metadata.tables["mailbox"]
    with e2e_write_env.engine.connect() as conn:
        before = conn.execute(
            select(mailbox.c.password).where(mailbox.c.username == "existing@write.example.com")
        ).scalar_one()

    code, out, err = _run(
        ["user", "passwd", "existing@write.example.com", "--claim"],
        e2e_write_env.env,
        input="newpassword\nnewpassword\n",
    )
    assert code == 0, f"stderr: {err}\nstdout: {out}"

    with e2e_write_env.engine.connect() as conn:
        after = conn.execute(
            select(mailbox.c.password).where(mailbox.c.username == "existing@write.example.com")
        ).scalar_one()
    # Password hash must have changed from the seed value ({NOAUTH} sentinel)
    assert after != before


def test_postino_user_disable(e2e_write_env: WriteEnv) -> None:
    """user disable sets active=0."""
    from sqlalchemy import select

    code, out, err = _run(
        ["user", "disable", "existing@write.example.com"],
        e2e_write_env.env,
    )
    assert code == 0, f"stderr: {err}\nstdout: {out}"

    mailbox = e2e_write_env.metadata.tables["mailbox"]
    with e2e_write_env.engine.connect() as conn:
        active = conn.execute(
            select(mailbox.c.active).where(mailbox.c.username == "existing@write.example.com")
        ).scalar_one()
    assert int(active) == 0  # type: ignore[arg-type]  # WHY: SQLAlchemy scalar_one returns Any


def test_postino_user_enable(e2e_write_env: WriteEnv) -> None:
    """user enable sets active=1 (round-trip with disable)."""
    from sqlalchemy import select

    # First disable so enable has something to do
    _run(["user", "disable", "existing@write.example.com"], e2e_write_env.env)

    code, out, err = _run(
        ["user", "enable", "existing@write.example.com"],
        e2e_write_env.env,
    )
    assert code == 0, f"stderr: {err}\nstdout: {out}"

    mailbox = e2e_write_env.metadata.tables["mailbox"]
    with e2e_write_env.engine.connect() as conn:
        active = conn.execute(
            select(mailbox.c.active).where(mailbox.c.username == "existing@write.example.com")
        ).scalar_one()
    assert int(active) == 1  # type: ignore[arg-type]  # WHY: SQLAlchemy scalar_one returns Any


def test_postino_user_quota_set(e2e_write_env: WriteEnv) -> None:
    """user quota --set updates the quota cap."""
    code, out, err = _run(
        ["--json", "user", "quota", "existing@write.example.com", "--set", "2G"],
        e2e_write_env.env,
    )
    assert code == 0, f"stderr: {err}\nstdout: {out}"
    data = _json_dict(out)
    # 2G = 2 * 1024^3
    assert int(data["quota_bytes"]) == 2 * 1024**3  # type: ignore[arg-type]  # WHY: data values are object; int() narrows safely


def test_postino_user_quota_show(e2e_write_env: WriteEnv) -> None:
    """user quota (no --set) shows current cap for the mailbox."""
    code, out, err = _run(
        ["--json", "user", "quota", "existing@write.example.com"],
        e2e_write_env.env,
    )
    assert code == 0, f"stderr: {err}\nstdout: {out}"
    data = _json_dict(out)
    assert data["username"] == "existing@write.example.com"


# ---------------------------------------------------------------------------
# alias write commands
# ---------------------------------------------------------------------------


def test_postino_alias_add(e2e_write_env: WriteEnv) -> None:
    """alias add creates a new alias row."""
    code, out, err = _run(
        [
            "--json",
            "alias",
            "add",
            "helpdesk@write.example.com",
            "existing@write.example.com",
        ],
        e2e_write_env.env,
    )
    assert code == 0, f"stderr: {err}\nstdout: {out}"
    data = _json_dict(out)
    assert data["address"] == "helpdesk@write.example.com"
    assert data["goto"] == "existing@write.example.com"


def test_postino_alias_add_duplicate_exits_2(e2e_write_env: WriteEnv) -> None:
    """alias add on an existing address exits 2 (AlreadyExistsError)."""
    code, _out, _err = _run(
        ["alias", "add", "info@write.example.com", "someone@write.example.com"],
        e2e_write_env.env,
    )
    assert code == 2


def test_postino_alias_del(e2e_write_env: WriteEnv) -> None:
    """alias del removes the alias row."""
    from sqlalchemy import select

    code, out, err = _run(
        ["alias", "del", "info@write.example.com", "--yes"],
        e2e_write_env.env,
    )
    assert code == 0, f"stderr: {err}\nstdout: {out}"

    alias = e2e_write_env.metadata.tables["alias"]
    with e2e_write_env.engine.connect() as conn:
        row = conn.execute(
            select(alias).where(alias.c.address == "info@write.example.com")
        ).fetchone()
    assert row is None


def test_postino_alias_del_nonexistent_exits_1(e2e_write_env: WriteEnv) -> None:
    """alias del on an unknown address exits 1 (NotFoundError)."""
    code, _out, _err = _run(
        ["alias", "del", "ghost@write.example.com", "--yes"],
        e2e_write_env.env,
    )
    assert code == 1


# ---------------------------------------------------------------------------
# passlib / bcrypt shim regression
# ---------------------------------------------------------------------------


def test_postino_user_add_no_passlib_trapped_warning(e2e_write_env: WriteEnv) -> None:
    """Regression: passlib's '(trapped) error reading bcrypt version' must not surface.

    bcrypt 4.1+ removed __about__; passlib 1.7.4 reads it. We shim
    bcrypt.__about__ in postino_core/__init__.py so passlib's backend
    load never trips. Production-visible: this warning leaked into
    `postino user add` interactive runs on m42 (vjt 2026-05-10).
    """
    code, out, err = _run(
        ["--json", "user", "add", "shim-test@write.example.com", "--quota", "100M"],
        e2e_write_env.env,
        input="testpw\ntestpw\n",
    )
    assert code == 0, f"stderr={err!r} stdout={out!r}"
    combined = out + err
    assert "(trapped)" not in combined, f"passlib trapped-warning leaked: {combined!r}"
    assert "__about__" not in combined, f"bcrypt __about__ AttributeError leaked: {combined!r}"
