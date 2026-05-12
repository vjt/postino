from __future__ import annotations

import pytest

from postino.exit import (
    _EXIT_CODES,  # pyright: ignore[reportPrivateUsage]  # WHY: defence-in-depth regression guard for exit-code mapping; module-private by design.
)
from postino_core.errors import (
    AlreadyExistsError,
    CapacityError,
    ConfigError,
    DBError,
    FilesystemError,
    HookError,
    MailctlError,
    MlmmjError,
    NotFoundError,
    RuleViolationError,
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


def test_mlmmj_error_inherits_mailctl_error() -> None:
    err = MlmmjError("mlmmj-make-ml exit 2: bad args")
    assert isinstance(err, MailctlError)
    assert "mlmmj-make-ml" in str(err)


def test_mlmmj_error_exits_with_code_9() -> None:
    """Defence-in-depth: regression guard against silent exit-code drift."""
    assert _EXIT_CODES[MlmmjError] == 9


def test_rule_violation_error_subclasses_mailctlerror() -> None:
    err = RuleViolationError("self-alias not allowed")
    assert isinstance(err, MailctlError)
    assert str(err) == "self-alias not allowed"


def test_rule_violation_error_distinct_from_other_errors() -> None:
    """RuleViolationError must not be caught by a generic ValueError handler."""
    err = RuleViolationError("x")
    assert not isinstance(err, ValueError)


def test_rule_violation_error_raises_cleanly() -> None:
    with pytest.raises(RuleViolationError, match="bad rule"):
        raise RuleViolationError("bad rule")


def test_rule_violation_error_exits_with_code_10() -> None:
    """Defence-in-depth: regression guard against silent exit-code drift."""
    assert _EXIT_CODES[RuleViolationError] == 10
