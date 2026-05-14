"""Unit tests for check_master_cf_mlmmj_pipes consistency check.

Validates that master.cf has all 4 v0.10 mlmmj pipe service blocks.
Help (`+help@`) rides the receive pipe via -e ${extension}; no separate
mlmmj-help binary exists in mlmmj 1.3+.
"""

from __future__ import annotations

from pathlib import Path

from postino_core.check.consistency import check_master_cf_mlmmj_pipes

_SPOOL = "/var/spool/mlmmj/$domain/$user"
_ALL_FOUR = (
    f"mlmmj-receive unix - n n - - pipe\n"
    f"   flags=DRhu user=mlmmj argv=/usr/bin/mlmmj-receive -L {_SPOOL} -e $extension\n"
    f"mlmmj-bounce unix - n n - - pipe\n"
    f"   flags=DRhu user=mlmmj argv=/usr/bin/mlmmj-bounce -L {_SPOOL}\n"
    f"mlmmj-sub unix - n n - - pipe\n"
    f"   flags=DRhu user=mlmmj argv=/usr/bin/mlmmj-sub -L {_SPOOL}\n"
    f"mlmmj-unsub unix - n n - - pipe\n"
    f"   flags=DRhu user=mlmmj argv=/usr/bin/mlmmj-unsub -L {_SPOOL}\n"
)


def test_check_lists_missing_master_cf_pipes(tmp_path: Path) -> None:
    master_cf = tmp_path / "master.cf"
    master_cf.write_text(
        "smtp inet n - n - - smtpd\n"
        "mlmmj-receive unix - n n - - pipe\n"
        f"   flags=DRhu user=mlmmj argv=/usr/bin/mlmmj-receive -L {_SPOOL} -e $extension\n"
    )
    findings = check_master_cf_mlmmj_pipes(master_cf)
    missing = [f for f in findings if f.severity == "error"]
    missing_names = {f.message for f in missing}
    # 3 of 4 missing
    assert any("mlmmj-bounce" in m for m in missing_names)
    assert any("mlmmj-sub" in m for m in missing_names)
    assert any("mlmmj-unsub" in m for m in missing_names)
    # WHY: mlmmj-help is intentionally NOT a required pipe — modern
    # mlmmj has no such binary; +help@ rides the receive pipe.
    assert not any("mlmmj-help" in m for m in missing_names)


def test_check_passes_when_all_four_pipes_present(tmp_path: Path) -> None:
    master_cf = tmp_path / "master.cf"
    master_cf.write_text("smtp inet n - n - - smtpd\n" + _ALL_FOUR)
    findings = check_master_cf_mlmmj_pipes(master_cf)
    assert all(f.severity == "info" for f in findings)
    assert len(findings) == 4


def test_check_errors_when_master_cf_missing(tmp_path: Path) -> None:
    master_cf = tmp_path / "master.cf"
    findings = check_master_cf_mlmmj_pipes(master_cf)
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "not found" in findings[0].message
