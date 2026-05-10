"""Every ``# type: ignore`` must carry a ``# WHY: ...`` justification.

Spec: prep action doc §"Type discipline rules" #3. The justification
makes future readers understand why pyright was overridden, and turns
the suppression into a maintenance debt instead of an invisible escape
hatch.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from .conftest import SourceFile, assert_violations_allowlisted, iter_source_files

# Match `# type: ignore` optionally followed by `[code]` codes,
# optionally followed by other text. We require `# WHY:` to appear
# elsewhere on the same line (before or after the `type: ignore`).
_TYPE_IGNORE = re.compile(r"#\s*type\s*:\s*ignore(?:\[[^\]]+\])?")
_WHY = re.compile(r"#\s*WHY:", re.IGNORECASE)


def _violations(src: SourceFile) -> Iterator[tuple[str, int, str]]:
    for lineno, line in enumerate(src.lines(), start=1):
        if not _TYPE_IGNORE.search(line):
            continue
        # Strip the `type: ignore` marker so an unrelated `# WHY:` *inside*
        # the ignore comment passes; we still demand a separate WHY token.
        stripped = _TYPE_IGNORE.sub("", line, count=1)
        if not _WHY.search(stripped):
            yield (
                src.relpath,
                lineno,
                "`# type: ignore` without `# WHY:` justification on the same line",
            )


def test_every_type_ignore_has_a_why() -> None:
    violations = (v for src in iter_source_files() for v in _violations(src))
    assert_violations_allowlisted("test_type_ignore_justified", violations)
