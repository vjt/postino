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


@pytest.mark.skipif(
    not (shutil.which("help2man") and shutil.which("mandoc")),
    reason="help2man and/or mandoc not installed",
)
def test_committed_manpages_match_build_script() -> None:
    """Run scripts/build-manpages.sh and confirm man/postino.1 + man/postinod.8 are unchanged."""
    postino_1 = MAN_DIR / "postino.1"
    postinod_8 = MAN_DIR / "postinod.8"
    before = {p: p.read_bytes() for p in (postino_1, postinod_8)}

    # Pin DATE to the committed postinod.8 .TH date so the build script's
    # default `date +%Y-%m-%d` doesn't cause spurious drift on day rollover.
    # Regex assumes the troff-template form (bare numeric section: `.TH POSTINOD 8 "DATE"`);
    # the help2man-generated postino.1 uses a quoted section (`.TH POSTINO "1" "DATE"`)
    # and is intentionally not used as the date source.
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
