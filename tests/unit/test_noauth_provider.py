"""NoAuthProvider unit tests."""

from __future__ import annotations

from postino_core.providers.noauth import NoAuthProvider


def test_noauth_release_is_noop() -> None:
    """NoAuthProvider.release_identity is a no-op."""
    p = NoAuthProvider()
    p.release_identity(conn=None, username="u@example.com")  # type: ignore[arg-type]  # WHY: noauth release is conn-agnostic no-op


def test_noauth_release_capability_false() -> None:
    """NoAuthProvider.supports_release_to_noauth returns False."""
    assert NoAuthProvider().supports_release_to_noauth() is False
