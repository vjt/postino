"""Zitadel event_type → service-layer dispatch (spec §3.6)."""

from __future__ import annotations

from postinod.zitadel.mapping import EventOutcome, dispatch_event


def test_user_human_added_maps_to_create() -> None:
    assert dispatch_event("user.human.added") == EventOutcome.CREATE


def test_profile_changed_maps_to_update() -> None:
    assert dispatch_event("user.human.profile.changed") == EventOutcome.UPDATE


def test_email_changed_rejected() -> None:
    assert dispatch_event("user.human.email.changed") == EventOutcome.REJECT


def test_deactivated_maps_to_disable() -> None:
    assert dispatch_event("user.deactivated") == EventOutcome.DISABLE


def test_reactivated_maps_to_enable() -> None:
    assert dispatch_event("user.reactivated") == EventOutcome.ENABLE


def test_removed_maps_to_disable() -> None:
    assert dispatch_event("user.removed") == EventOutcome.DISABLE


def test_unknown_event_type_ignored() -> None:
    assert dispatch_event("user.something.weird") == EventOutcome.IGNORE
