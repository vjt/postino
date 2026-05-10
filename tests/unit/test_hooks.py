from pathlib import Path

import pytest

from postino_core.errors import HookError
from postino_core.hooks import HookRunner


def _run(runner: HookRunner) -> None:
    """Invoke runner with the canonical four-arg PA contract."""
    runner.run_postcreation(
        username="foo@example.com",
        domain="example.com",
        maildir="example.com/foo/",
        quota=0,
    )


def test_hook_success(tmp_path: Path) -> None:
    log = tmp_path / "log"
    script = tmp_path / "h.sh"
    # Log all four PA args so we can assert the contract is passed correctly.
    script.write_text(f'#!/bin/sh\necho "$@" > {log}\nexit 0\n')
    script.chmod(0o755)
    _run(HookRunner(script_path=script))
    assert log.read_text().strip() == "foo@example.com example.com example.com/foo/ 0"


def test_hook_nonzero_raises(tmp_path: Path) -> None:
    script = tmp_path / "h.sh"
    script.write_text("#!/bin/sh\nexit 7\n")
    script.chmod(0o755)
    with pytest.raises(HookError):
        _run(HookRunner(script_path=script))


def test_hook_missing_script_raises(tmp_path: Path) -> None:
    with pytest.raises(HookError):
        _run(HookRunner(script_path=tmp_path / "no"))


def test_hook_timeout_raises_HookError(tmp_path: Path) -> None:
    script = tmp_path / "h.sh"
    script.write_text("#!/bin/sh\nsleep 5\nexit 0\n")
    script.chmod(0o755)
    runner = HookRunner(script_path=script, timeout=0.5)
    with pytest.raises(HookError, match="timed out"):
        _run(runner)


def test_hook_default_timeout_attribute(tmp_path: Path) -> None:
    runner = HookRunner(script_path=tmp_path / "x")
    assert runner.timeout == 30.0


def test_hook_logs_stdout_on_success(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    script = tmp_path / "h.sh"
    script.write_text('#!/bin/sh\necho "hookoutput"\nexit 0\n')
    script.chmod(0o755)
    with caplog.at_level(logging.INFO, logger="postino_core.hooks"):
        _run(HookRunner(script_path=script))
    assert any("hookoutput" in rec.message for rec in caplog.records)


def test_hook_error_includes_stdout(tmp_path: Path) -> None:
    """HookError message includes stdout — m42 hook prints diagnostics to stdout."""
    script = tmp_path / "h.sh"
    script.write_text("#!/bin/sh\necho 'basedir missing'\nexit 1\n")
    script.chmod(0o755)
    with pytest.raises(HookError, match="basedir missing"):
        _run(HookRunner(script_path=script))


def test_hook_receives_four_pa_args(tmp_path: Path) -> None:
    """Positional args: USERNAME DOMAIN MAILDIR QUOTA (PA-style contract)."""
    log = tmp_path / "args.log"
    script = tmp_path / "h.sh"
    script.write_text(f'#!/bin/sh\necho "$1|$2|$3|$4" > {log}\nexit 0\n')
    script.chmod(0o755)
    HookRunner(script_path=script).run_postcreation(
        username="alice@example.net",
        domain="example.net",
        maildir="example.net/alice/",
        quota=5368709120,
    )
    parts = log.read_text().strip().split("|")
    assert parts == ["alice@example.net", "example.net", "example.net/alice/", "5368709120"]
