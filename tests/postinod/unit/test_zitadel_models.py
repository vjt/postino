"""Zitadel event payload models — pydantic strict round-trip."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from postinod.zitadel.models import (
    HumanProfilePayload,
    LifecyclePayload,
    UserAddedPayload,
    ZitadelEvent,
)


def _wrap(event_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "aggregateID": "agg-1",
        "userID": "user-1",
        "event_type": event_type,
        "created_at": "2026-05-10T11:00:00Z",
        "event_payload": payload,
    }


def test_user_added_event_round_trip() -> None:
    raw = _wrap(
        "user.human.added",
        {"email": "alice@example.org", "firstName": "Alice", "lastName": "Rossi", "active": True},
    )
    e = ZitadelEvent.model_validate(raw)
    assert e.event_type == "user.human.added"
    assert isinstance(e.event_payload, UserAddedPayload)
    assert e.event_payload.email == "alice@example.org"
    assert e.event_payload.first_name == "Alice"


def test_profile_changed_requires_email_per_decision() -> None:
    """vjt 2026-05-11: Action target templates email into all payloads."""
    raw = _wrap(
        "user.human.profile.changed",
        {"email": "carol@example.org", "firstName": "Carol", "lastName": "X"},
    )
    e = ZitadelEvent.model_validate(raw)
    assert isinstance(e.event_payload, HumanProfilePayload)
    assert e.event_payload.email == "carol@example.org"


def test_profile_changed_without_email_rejected() -> None:
    """If operator forgot to template email into Action body, validation fails loudly."""
    raw = _wrap(
        "user.human.profile.changed",
        {"firstName": "Carol", "lastName": "X"},  # no email
    )
    with pytest.raises(ValidationError):
        ZitadelEvent.model_validate(raw)


def test_lifecycle_event_requires_email() -> None:
    raw = _wrap("user.deactivated", {"email": "alice@example.org"})
    e = ZitadelEvent.model_validate(raw)
    assert isinstance(e.event_payload, LifecyclePayload)
    assert e.event_payload.email == "alice@example.org"


def test_lifecycle_event_without_email_rejected() -> None:
    raw = _wrap("user.removed", {})
    with pytest.raises(ValidationError):
        ZitadelEvent.model_validate(raw)


def test_unknown_event_type_keeps_payload_as_dict() -> None:
    raw = _wrap("user.something.weird", {"whatever": 42})
    e = ZitadelEvent.model_validate(raw)
    assert e.event_type == "user.something.weird"
    assert e.event_payload == {"whatever": 42}


def test_invalid_email_rejected() -> None:
    raw = _wrap(
        "user.human.added",
        {"email": "not-an-email", "firstName": "x", "lastName": "y", "active": True},
    )
    with pytest.raises(ValidationError):
        ZitadelEvent.model_validate(raw)


def test_missing_required_field_rejected() -> None:
    with pytest.raises(ValidationError):
        ZitadelEvent.model_validate({"event_type": "user.human.added"})
