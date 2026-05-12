"""Settings → provider dispatch under hybrid."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import MetaData

from postino_core.enums import IdentityBackend
from postino_core.providers import HybridProvider, LocalProvider, NoAuthProvider
from postino_core.services.bundle import (
    provider_for,  # pyright: ignore[reportPrivateUsage]  # WHY: testing private dispatch logic that must be verified per-backend.
)


def _clock() -> datetime:
    return datetime.now(UTC)


def test_dispatch_local() -> None:
    p = provider_for(IdentityBackend.LOCAL, metadata=MetaData(), clock=_clock)
    assert isinstance(p, LocalProvider)


def test_dispatch_noauth() -> None:
    p = provider_for(IdentityBackend.NOAUTH, metadata=MetaData(), clock=_clock)
    assert isinstance(p, NoAuthProvider)


def test_dispatch_hybrid() -> None:
    p = provider_for(IdentityBackend.HYBRID, metadata=MetaData(), clock=_clock)
    assert isinstance(p, HybridProvider)
