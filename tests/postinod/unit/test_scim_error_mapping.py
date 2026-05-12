"""postino_core exception → SCIM Error response (spec §4.5)."""

from __future__ import annotations

import pytest

from postino_core.errors import (
    AlreadyExistsError,
    CapacityError,
    ConfigError,
    DBError,
    FilesystemError,
    HookError,
    NotFoundError,
)
from postinod.scim.errors import scim_error_from_exception

_INTERNAL = "internal error"


@pytest.mark.parametrize(
    "exc, status, scim_type, detail",
    [
        (NotFoundError("missing"), 404, None, "missing"),
        (AlreadyExistsError("dup"), 409, "uniqueness", "dup"),
        (CapacityError("cap"), 400, "tooMany", "cap"),
        (ConfigError("bad"), 400, "invalidValue", "bad"),
        # Internal mutator errors map to a generic detail; the
        # underlying str() (DBAPI args, maildir paths, hook stderr) is
        # logged server-side and must never reach the HTTP body.
        (FilesystemError("io"), 500, None, _INTERNAL),
        (HookError("hook"), 500, None, _INTERNAL),
        (DBError("db"), 500, None, _INTERNAL),
    ],
)
def test_exception_mapping(exc: Exception, status: int, scim_type: str | None, detail: str) -> None:
    err = scim_error_from_exception(exc)
    assert err.status == str(status)
    assert err.scim_type == scim_type
    assert err.detail == detail


def test_create_path_not_found_is_400_invalid_value() -> None:
    """Per spec §4.5 NotFoundError on domain-during-create is 400 invalidValue, not 404."""
    err = scim_error_from_exception(
        NotFoundError("domain example.org does not exist"), create_path=True
    )
    assert err.status == "400"
    assert err.scim_type == "invalidValue"
