"""Fixtures shared by the CLI tests."""

from __future__ import annotations

import io
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from postino.commands import user as user_cmd


class _TTYStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture
def tty_stdin(monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    """Call it to make ``postino.commands.user`` see an interactive stdin
    from then on (a test may first need a piped one, e.g. to seed a user).

    ``CliRunner.invoke`` replaces ``sys.stdin`` with a stream built from
    ``input=``, and since typer 0.27 that must be ``str`` or ``bytes``: a
    stream reporting ``isatty()`` can no longer be passed in, and
    patching ``sys.stdin`` before invoke() is undone by the swap. So the
    command module's own ``sys`` reference is replaced instead.
    """

    def enable() -> None:
        monkeypatch.setattr(user_cmd, "sys", SimpleNamespace(stdin=_TTYStringIO()))

    return enable
