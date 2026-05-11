"""postinod boot guard: identity-contract enforcement."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import MetaData

from postino_core.enums import IdentityBackend
from postino_core.errors import ConfigError
from postino_core.providers import HybridProvider, LocalProvider, NoAuthProvider
from postinod.app import (
    _enforce_identity_contract,  # type: ignore[name-defined]  # WHY: private test fixture
)


class _StubSettings:
    def __init__(self, backend: IdentityBackend) -> None:
        self.identity_backend = backend


def _clock() -> datetime:
    return datetime.now(UTC)


def test_noauth_with_noauth_provider_passes() -> None:
    _enforce_identity_contract(_StubSettings(IdentityBackend.NOAUTH), NoAuthProvider())  # type: ignore[arg-type]  # WHY: stub for settings; we only need .identity_backend


def test_noauth_with_local_provider_raises() -> None:
    p = LocalProvider(metadata=MetaData(), clock=_clock)
    with pytest.raises(ConfigError, match="identity_backend=noauth"):
        _enforce_identity_contract(_StubSettings(IdentityBackend.NOAUTH), p)  # type: ignore[arg-type]  # WHY: stub for settings; we only need .identity_backend


def test_noauth_with_hybrid_provider_raises() -> None:
    p = HybridProvider(metadata=MetaData(), clock=_clock)
    with pytest.raises(ConfigError, match="identity_backend=noauth"):
        _enforce_identity_contract(_StubSettings(IdentityBackend.NOAUTH), p)  # type: ignore[arg-type]  # WHY: stub for settings; we only need .identity_backend


def test_hybrid_with_hybrid_provider_passes() -> None:
    p = HybridProvider(metadata=MetaData(), clock=_clock)
    _enforce_identity_contract(_StubSettings(IdentityBackend.HYBRID), p)  # type: ignore[arg-type]  # WHY: stub for settings; we only need .identity_backend


def test_local_with_local_provider_passes() -> None:
    p = LocalProvider(metadata=MetaData(), clock=_clock)
    _enforce_identity_contract(_StubSettings(IdentityBackend.LOCAL), p)  # type: ignore[arg-type]  # WHY: stub for settings; we only need .identity_backend
