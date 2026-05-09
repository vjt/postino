"""QuotaService — read-only view of the PA quota2 table."""
from __future__ import annotations

from pydantic import EmailStr
from sqlalchemy import MetaData, select
from sqlalchemy.engine import Engine

from postino_core.models import MailboxUsage


class QuotaService:
    def __init__(self, *, engine: Engine, metadata: MetaData) -> None:
        self._engine = engine
        self._md = metadata

    def show(self, username: EmailStr) -> MailboxUsage | None:
        q = self._md.tables["quota2"]
        with self._engine.connect() as conn:
            row = conn.execute(
                select(q).where(q.c.username == str(username))
            ).fetchone()
        if row is None:
            return None
        m = row._mapping  # type: ignore[attr-defined]
        return MailboxUsage(
            username=m["username"],  # type: ignore[index]
            bytes_used=int(m["bytes"]),  # type: ignore[index]
            messages=int(m["messages"]),  # type: ignore[index]
        )

    def list(self) -> list[MailboxUsage]:
        q = self._md.tables["quota2"]
        with self._engine.connect() as conn:
            rows = conn.execute(select(q).order_by(q.c.username)).fetchall()
        return [
            MailboxUsage(
                username=r._mapping["username"],  # type: ignore[index]
                bytes_used=int(r._mapping["bytes"]),  # type: ignore[index]
                messages=int(r._mapping["messages"]),  # type: ignore[index]
            )
            for r in rows
        ]
