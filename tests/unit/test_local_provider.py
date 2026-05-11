"""LocalProvider unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

from postino_core.errors import ConfigError
from postino_core.providers.local import LocalProvider


def test_local_provider_release_raises(
    db: Engine,
) -> None:
    """LocalProvider.release_identity raises ConfigError."""
    p = LocalProvider(metadata=MetaData(), clock=lambda: datetime.now(UTC))
    with db.begin() as conn:
        with pytest.raises(ConfigError, match="local backend does not release"):
            p.release_identity(conn, "u@example.com")


def test_local_provider_release_capability_false() -> None:
    """LocalProvider.supports_release_to_noauth returns False."""
    p = LocalProvider(metadata=MetaData(), clock=lambda: datetime.now(UTC))
    assert p.supports_release_to_noauth() is False
