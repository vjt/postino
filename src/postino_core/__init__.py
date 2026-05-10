"""postino_core — provider-agnostic library for PostfixAdmin schema CRUD."""

# Shim bcrypt.__about__ for passlib 1.7.4 compatibility.
#
# passlib's bcrypt backend reads `bcrypt.__about__.__version__` on load.
# bcrypt 4.1+ removed the `__about__` submodule (its package metadata moved to
# the standard pyproject.toml location). passlib catches the AttributeError
# and falls through to a working code path, but logs "(trapped) error reading
# bcrypt version" at WARNING. The hash succeeds, but the noise leaks into
# every CLI invocation.
#
# Synthesize the missing attribute from bcrypt.__version__ before passlib
# loads its backend. Effective once postino_core is imported, which happens
# before any provider call.
from __future__ import annotations

import types

import bcrypt as _bcrypt

if not hasattr(_bcrypt, "__about__"):
    _ver: str = str(getattr(_bcrypt, "__version__", ""))  # __version__ is untyped in bcrypt stubs
    _bcrypt.__about__ = types.SimpleNamespace(  # type: ignore[attr-defined]  # WHY: synthesizing the legacy attribute that passlib 1.7.4 reads; bcrypt 4.1+ removed __about__ but kept __version__.
        __version__=_ver,
    )
