"""StatusService — read-only row-count snapshot of the PA schema.

Pure data: SQL stays inside ``postino_core``; rendering (Rich tables /
JSON) belongs to the CLI layer.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from sqlalchemy import MetaData, func, select
from sqlalchemy.engine import Engine

_TABLES = ("domain", "mailbox", "alias", "quota2")


class StatusReport(BaseModel):
    """Row counts for the PostfixAdmin tables postino administers."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    domains: int
    mailboxes: int
    aliases: int
    quota2: int


class StatusService:
    """SELECT COUNT(*) over the four tables postino exposes."""

    def __init__(self, *, engine: Engine, metadata: MetaData) -> None:
        self._engine = engine
        self._md = metadata

    def snapshot(self) -> StatusReport:
        counts: dict[str, int] = {}
        with self._engine.connect() as conn:
            for table_name in _TABLES:
                t = self._md.tables[table_name]
                counts[table_name] = int(
                    conn.execute(select(func.count()).select_from(t)).scalar_one()
                )
        return StatusReport(
            domains=counts["domain"],
            mailboxes=counts["mailbox"],
            aliases=counts["alias"],
            quota2=counts["quota2"],
        )
