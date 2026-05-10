"""Zitadel event_type → service-layer outcome (spec §3.6).

Data-only: the router (Task 9) inspects the outcome and calls the
appropriate MailboxService method. Pure-data form keeps the dispatch
table trivially auditable.
"""

from __future__ import annotations

from enum import StrEnum


class EventOutcome(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DISABLE = "disable"
    ENABLE = "enable"
    REJECT = "reject"
    IGNORE = "ignore"


_TABLE: dict[str, EventOutcome] = {
    "user.human.added": EventOutcome.CREATE,
    "user.human.profile.changed": EventOutcome.UPDATE,
    "user.human.email.changed": EventOutcome.REJECT,
    "user.deactivated": EventOutcome.DISABLE,
    "user.reactivated": EventOutcome.ENABLE,
    "user.removed": EventOutcome.DISABLE,
}


def dispatch_event(event_type: str) -> EventOutcome:
    """Return the postinod outcome for a Zitadel event_type."""
    return _TABLE.get(event_type, EventOutcome.IGNORE)
