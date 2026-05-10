import pytest

from postino_core.errors import (
    AlreadyExistsError,
    CapacityError,
    ConfigError,
    DBError,
    FilesystemError,
    HookError,
    MailctlError,
    NotFoundError,
)


def test_all_errors_subclass_mailctl() -> None:
    for cls in (
        ConfigError,
        DBError,
        NotFoundError,
        AlreadyExistsError,
        CapacityError,
        FilesystemError,
        HookError,
    ):
        assert issubclass(cls, MailctlError)


def test_mailctl_error_subclasses_exception() -> None:
    assert issubclass(MailctlError, Exception)


def test_error_carries_message() -> None:
    err = NotFoundError("user@dom not found")
    assert str(err) == "user@dom not found"


def test_caught_as_mailctl() -> None:
    with pytest.raises(MailctlError):
        raise CapacityError("max mailboxes reached")
