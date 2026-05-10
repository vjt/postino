"""Engine-factory tests focused on credential redaction.

Connecting requires a running database, but ``URL.create`` and the
returned ``Engine.url`` repr paths run without any I/O — that's enough
to lock in the no-leak guarantee.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from postino_core.config import PostfixSqlCredentials
from postino_core.db import make_engine, translate_db_errors
from postino_core.errors import DeadlockError


def _creds(password: str) -> PostfixSqlCredentials:
    return PostfixSqlCredentials(
        host="db.example.org",
        user="postfix",
        password=SecretStr(password),
        dbname="postfix",
    )


def test_make_engine_url_redacts_password() -> None:
    engine = _engine_no_connect("hunter2-leak-probe")
    try:
        url_str = str(engine.url)
        url_repr = repr(engine.url)
        engine_repr = repr(engine)
        assert "hunter2-leak-probe" not in url_str
        assert "hunter2-leak-probe" not in url_repr
        assert "hunter2-leak-probe" not in engine_repr
        # SQLAlchemy renders the password as ``***`` in str/repr paths.
        assert "***" in url_str
    finally:
        engine.dispose()


def test_make_engine_render_password_returns_cleartext() -> None:
    engine = _engine_no_connect("hunter2-leak-probe")
    try:
        cleartext = engine.url.render_as_string(hide_password=False)
        assert "hunter2-leak-probe" in cleartext
    finally:
        engine.dispose()


def _engine_no_connect(password: str) -> Engine:
    return make_engine(_creds(password), echo=False)


class _FakePyMySQLError(Exception):
    """PyMySQL's OperationalError shape: args=(code, message)."""


def _fake_operational(code: int, message: str) -> OperationalError:
    """Build an OperationalError carrying a PyMySQL-style numeric code.

    SQLAlchemy passes the wrapped DB-API exception through ``orig``;
    PyMySQL puts the code at ``orig.args[0]``."""
    orig = _FakePyMySQLError(code, message)
    return OperationalError(statement="SELECT 1", params=None, orig=orig)


def test_translate_db_errors_deadlock_becomes_DeadlockError() -> None:
    with pytest.raises(DeadlockError):  # noqa: SIM117 - explicit boundary semantics
        with translate_db_errors():
            raise _fake_operational(1213, "Deadlock found when trying to get lock")


def test_translate_db_errors_lock_wait_timeout_becomes_DeadlockError() -> None:
    with pytest.raises(DeadlockError):  # noqa: SIM117
        with translate_db_errors():
            raise _fake_operational(1205, "Lock wait timeout exceeded")


def test_translate_db_errors_other_OperationalError_propagates() -> None:
    with pytest.raises(OperationalError):  # noqa: SIM117
        with translate_db_errors():
            raise _fake_operational(2002, "Can't connect to MySQL server")
