"""Manpages committed in man/ must match what build-manpages.sh produces."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
MAN_DIR = REPO_ROOT / "man"
SCRIPT = REPO_ROOT / "scripts" / "build-manpages.sh"


def _have(binary: str) -> bool:
    return shutil.which(binary) is not None


@pytest.mark.skipif(
    not (_have("help2man") and _have("mandoc")),
    reason="help2man and/or mandoc not installed",
)
def test_committed_manpages_match_build_script(tmp_path: Path) -> None:
    """Run scripts/build-manpages.sh and confirm man/postino.1 + man/postinod.8 are unchanged."""
    # Snapshot current content so we can restore if the test fails or the script mutated them.
    postino_1 = MAN_DIR / "postino.1"
    postinod_8 = MAN_DIR / "postinod.8"
    before = {p: p.read_bytes() for p in (postino_1, postinod_8)}

    # Pin DATE to the date in the committed postinod.8 .TH line so the script's
    # daily date stamp doesn't introduce false drift.
    th_match = re.search(rb'^\.TH \S+ \d+ "([\d-]+)"', before[postinod_8], re.MULTILINE)
    assert th_match is not None, "could not parse .TH date from committed postinod.8"
    pinned_date = th_match.group(1).decode()

    env = os.environ.copy()
    env["DATE"] = pinned_date

    try:
        subprocess.run(
            [str(SCRIPT)],
            cwd=REPO_ROOT,
            env=env,
            check=True,
            capture_output=True,
        )

        after = {p: p.read_bytes() for p in (postino_1, postinod_8)}

        stale: list[str] = []
        for p in (postino_1, postinod_8):
            if before[p] != after[p]:
                stale.append(p.name)

        assert not stale, (
            f"Committed manpages are stale: {stale}. "
            "Run `./scripts/build-manpages.sh` and commit the regenerated files."
        )
    finally:
        # Always restore — even if the script bombed mid-run or the assertion fired.
        for p, content in before.items():
            p.write_bytes(content)
