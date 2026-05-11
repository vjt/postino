"""IdentityProvider Protocol shape."""

from __future__ import annotations

import inspect

from postino_core.providers.base import IdentityProvider


def test_protocol_declares_release_identity() -> None:
    assert hasattr(IdentityProvider, "release_identity")
    sig = inspect.signature(IdentityProvider.release_identity)
    params = list(sig.parameters)
    assert params == ["self", "conn", "username"]


def test_protocol_declares_release_capability() -> None:
    assert hasattr(IdentityProvider, "supports_release_to_noauth")
