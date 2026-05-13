"""Unit tests for check_recipient_delimiter consistency check.

Validates that main.cf recipient_delimiter contains both '+' and '-'
as required by v0.10+ mlmmj hyphen-suffix dispatch.
"""

from __future__ import annotations

from pathlib import Path

from postino_core.check.consistency import check_recipient_delimiter


def test_check_warns_when_recipient_delimiter_missing_hyphen(tmp_path: Path) -> None:
    main_cf = tmp_path / "main.cf"
    main_cf.write_text("recipient_delimiter = +\n")
    findings = check_recipient_delimiter(main_cf)
    assert any(f.severity == "error" and "-" in f.message for f in findings)


def test_check_passes_when_recipient_delimiter_has_plus_and_hyphen(tmp_path: Path) -> None:
    main_cf = tmp_path / "main.cf"
    main_cf.write_text("recipient_delimiter = +-\n")
    findings = check_recipient_delimiter(main_cf)
    assert all(f.severity == "info" for f in findings)
