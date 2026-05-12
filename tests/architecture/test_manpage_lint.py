"""mandoc(1) lint each shipped manpage; warnings fail CI."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

MAN_DIR = Path(__file__).resolve().parents[2] / "man"


@pytest.mark.skipif(shutil.which("mandoc") is None, reason="mandoc not installed")
@pytest.mark.parametrize("page", ["postino.1", "postinod.8"])
def test_manpage_lints_clean(page: str) -> None:
    result = subprocess.run(
        ["mandoc", "-Tlint", str(MAN_DIR / page)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"mandoc -Tlint {page} failed:\n{result.stdout}\n{result.stderr}"
    assert result.stdout == "", f"mandoc warnings on {page}:\n{result.stdout}"
