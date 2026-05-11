"""Identity provider implementations + the public sentinel constant."""

from postino_core.providers.base import SENTINEL_NOAUTH, IdentityProvider
from postino_core.providers.hybrid import HybridProvider
from postino_core.providers.local import LocalProvider
from postino_core.providers.noauth import NoAuthProvider

__all__ = [
    "SENTINEL_NOAUTH",
    "HybridProvider",
    "IdentityProvider",
    "LocalProvider",
    "NoAuthProvider",
]
