"""postino_core exception → SCIM Error response.

Spec §4.5 table; create-path NotFoundError is 400 invalidValue (the
referenced domain doesn't exist) while general NotFoundError is 404.
"""

from __future__ import annotations

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


def scim_error_from_exception(exc: Exception, *, create_path: bool = False) -> ScimError:
    detail = str(exc)
    if isinstance(exc, NotFoundError):
        if create_path:
            return ScimError(status="400", scimType="invalidValue", detail=detail)
        return ScimError(status="404", detail=detail)
    if isinstance(exc, AlreadyExistsError):
        return ScimError(status="409", scimType="uniqueness", detail=detail)
    if isinstance(exc, CapacityError):
        return ScimError(status="400", scimType="tooMany", detail=detail)
    if isinstance(exc, ConfigError):
        return ScimError(status="400", scimType="invalidValue", detail=detail)
    if isinstance(exc, FilesystemError | HookError | DBError):
        return ScimError(status="500", detail=detail)
    return ScimError(status="500", detail=detail)
