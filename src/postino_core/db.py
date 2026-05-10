"""SQLAlchemy 2.0 engine factory + schema reflection.

Reflection is done once per Engine and cached in `MetaData`. Tables
are exposed by attribute (`db.metadata.tables['mailbox']`) so services
can compose insert/update/select without redefining column lists."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import URL, MetaData, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from postino_core.config import PostfixSqlCredentials
from postino_core.errors import DeadlockError

_MYSQL_DEADLOCK_CODES = frozenset({1213, 1205})


def _is_mysql_deadlock(exc: OperationalError) -> bool:
    """Identify MySQL deadlock (1213) and lock-wait-timeout (1205).

    PyMySQL exposes the numeric error code as ``OperationalError.orig.args[0]``;
    other DB-API drivers may shape the args list differently — guard
    against an unexpected layout instead of raising IndexError."""
    orig = exc.orig
    if orig is None:
        return False
    args = getattr(orig, "args", ())
    return bool(args) and args[0] in _MYSQL_DEADLOCK_CODES


@contextmanager
def translate_db_errors() -> Generator[None]:
    """Catch SQLAlchemy OperationalError and surface MySQL deadlocks /
    lock-wait timeouts as DeadlockError; let other OperationalError
    flavours propagate unchanged so DBError-mapping at the CLI catches
    them with their original message."""
    try:
        yield
    except OperationalError as e:
        if _is_mysql_deadlock(e):
            raise DeadlockError(f"MySQL deadlock / lock-wait timeout: {e.orig}") from e
        raise


def make_engine(creds: PostfixSqlCredentials, *, echo: bool) -> Engine:
    """Create a SQLAlchemy 2.0 Engine for the given creds.

    The URL is built with ``sqlalchemy.URL.create`` rather than an
    embedded-creds f-string so the password lives on the URL object
    (which redacts on repr/str) rather than in a free-form string that
    leaks through ``OperationalError`` messages.

    Returns: Engine. Caller owns disposal."""
    url = URL.create(
        drivername="mysql+pymysql",
        username=creds.user,
        password=creds.password.get_secret_value(),
        host=creds.host,
        database=creds.dbname,
    )
    return create_engine(url, echo=echo, future=True)


def reflect_schema(engine: Engine) -> MetaData:
    """Reflect all PostfixAdmin tables we touch into MetaData.

    Returns: a populated MetaData instance.
    """
    metadata = MetaData()
    metadata.reflect(
        bind=engine,
        only=(
            "mailbox",
            "alias",
            "alias_domain",
            "domain",
            "domain_admins",
            "quota2",
            "log",
        ),
    )
    return metadata
