"""SQLAlchemy 2.0 engine factory + schema reflection.

Reflection is done once per Engine and cached in `MetaData`. Tables
are exposed by attribute (`db.metadata.tables['mailbox']`) so services
can compose insert/update/select without redefining column lists."""

from __future__ import annotations

from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import Engine

from postino_core.config import PostfixSqlCredentials


def make_engine(creds: PostfixSqlCredentials, *, echo: bool) -> Engine:
    """Create a SQLAlchemy 2.0 Engine for the given creds.

    Returns: Engine. Caller owns disposal."""
    return create_engine(creds.sqlalchemy_url(), echo=echo, future=True)


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
