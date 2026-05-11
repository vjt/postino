"""Regression guard for CLI exit-code mapping.

Parametrised over every `MailctlError` subclass. Asserts:

* Every concrete subclass has a row in `_EXIT_CODES` — adding a new
  exception without wiring up its exit code now fails this test
  instead of silently bucketing into the `99` (uncaught) bucket.
* `exit_with_error(err)` raises `SystemExit` with the documented
  code and writes the message to stderr.

Pair with `tests/unit/test_errors.py` (legacy spec tests) — this file
is the table-driven one called out in v0.4 Task 6.
"""

from __future__ import annotations

import pytest

from postino.cli import (
    _EXIT_CODES,  # pyright: ignore[reportPrivateUsage]  # WHY: defence-in-depth regression guard for exit-code mapping; module-private by design.
    exit_with_error,
)
from postino_core.errors import MailctlError


def _all_concrete_mailctl_subclasses() -> set[type[MailctlError]]:
    """Walk `MailctlError.__subclasses__()` transitively, gathering leaves."""
    seen: set[type[MailctlError]] = set()
    stack: list[type[MailctlError]] = list(MailctlError.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        stack.extend(cls.__subclasses__())
    return seen


def test_every_mailctl_subclass_has_an_exit_code() -> None:
    """No silent drift into the 99 bucket: every subclass maps to a code."""
    subclasses = _all_concrete_mailctl_subclasses()
    missing = sorted(c.__name__ for c in subclasses if c not in _EXIT_CODES)
    assert not missing, (
        f"MailctlError subclass(es) missing from _EXIT_CODES: {missing}. "
        "Add a row in postino/cli.py and a README exit-code entry."
    )


def test_exit_codes_are_unique() -> None:
    """Distinct subclasses must map to distinct codes; collisions corrupt scripting."""
    codes = list(_EXIT_CODES.values())
    assert len(codes) == len(set(codes)), f"duplicate exit codes in mapping: {codes}"


@pytest.mark.parametrize(("cls", "code"), list(_EXIT_CODES.items()))
def test_exit_with_error_writes_message_and_exits_with_documented_code(
    cls: type[MailctlError],
    code: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    msg = f"test-{cls.__name__}"
    with pytest.raises(SystemExit) as exc_info:
        exit_with_error(cls(msg))
    assert exc_info.value.code == code
    captured = capsys.readouterr()
    assert msg in captured.err
    assert "error:" in captured.err.lower()
