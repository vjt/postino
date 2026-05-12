"""postino_core exception → SCIM Error response.

Spec §4.5 table; create-path NotFoundError is 400 invalidValue (the
referenced domain doesn't exist) while general NotFoundError is 404.

Internal mutator failures (DBError/FilesystemError/HookError) map to
500 with a generic ``detail`` — the underlying exception ``str()``
(which can carry DBAPI args, table names, maildir paths) is logged
server-side at the call site and never leaks into the HTTP body.
"""

from __future__ import annotations

from pydantic import ValidationError

from postino_core.errors import (
    AlreadyExistsError,
    CapacityError,
    ConfigError,
    DBError,
    FilesystemError,
    HookError,
    NotFoundError,
)
from postinod.scim.models import ScimError

_INTERNAL_ERROR_DETAIL = "internal error"


def scim_error_from_exception(exc: Exception, *, create_path: bool = False) -> ScimError:
    if isinstance(exc, NotFoundError):
        if create_path:
            return ScimError(status="400", scimType="invalidValue", detail=str(exc))
        return ScimError(status="404", detail=str(exc))
    if isinstance(exc, AlreadyExistsError):
        return ScimError(status="409", scimType="uniqueness", detail=str(exc))
    if isinstance(exc, CapacityError):
        return ScimError(status="400", scimType="tooMany", detail=str(exc))
    if isinstance(exc, ConfigError):
        return ScimError(status="400", scimType="invalidValue", detail=str(exc))
    if isinstance(exc, FilesystemError | HookError | DBError):
        return ScimError(status="500", detail=_INTERNAL_ERROR_DETAIL)
    return ScimError(status="500", detail=_INTERNAL_ERROR_DETAIL)


def scim_validation_detail(err: ValidationError) -> str:
    """Pydantic ValidationError → SCIM-safe ``detail`` string.

    ``str(ValidationError)`` includes the failing input value, which
    for password / token / similar fields would leak the rejected
    secret into the HTTP error body. Use ``include_input=False`` and
    drop URLs so the SCIM client sees only location + reason.
    """
    parts: list[str] = []
    for entry in err.errors(include_url=False, include_input=False):
        loc = ".".join(str(p) for p in entry.get("loc", ()))
        msg = entry.get("msg", "validation error")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts) or "validation error"
