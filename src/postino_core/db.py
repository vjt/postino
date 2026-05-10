"""SQLAlchemy 2.0 engine factory + schema reflection.

Reflection is done once per Engine and cached in `MetaData`. Tables
are exposed by attribute (`db.metadata.tables['mailbox']`) so services
can compose insert/update/select without redefining column lists."""

from __future__ import annotations

from sqlalchemy import URL, MetaData, create_engine
from sqlalchemy.engine import Engine

from postino_core.config import PostfixSqlCredentials


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
