from pathlib import Path

import pytest

from postino_core.errors import HookError
from postino_core.hooks import HookRunner


def test_hook_success(tmp_path: Path) -> None:
    log = tmp_path / "log"
    script = tmp_path / "h.sh"
    script.write_text(f'#!/bin/sh\necho "$@" > {log}\nexit 0\n')
    script.chmod(0o755)
    HookRunner(script_path=script).run_postcreation("foo@example.com")
    assert log.read_text().strip() == "foo@example.com"


def test_hook_nonzero_raises(tmp_path: Path) -> None:
    script = tmp_path / "h.sh"
    script.write_text("#!/bin/sh\nexit 7\n")
    script.chmod(0o755)
    with pytest.raises(HookError):
        HookRunner(script_path=script).run_postcreation("foo@example.com")


def test_hook_missing_script_raises(tmp_path: Path) -> None:
    with pytest.raises(HookError):
        HookRunner(script_path=tmp_path / "no").run_postcreation("foo@example.com")
