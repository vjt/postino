"""Unit tests for check_postfix_transport_maps consistency check.

Validates that main.cf transport_maps wiring is correct for v0.10+
mlmmj routing: mysql:sql-routes.cf must appear FIRST, before the
per-domain catchall mysql:sql-virtual_transport.cf.
"""

from __future__ import annotations

from pathlib import Path

from postino_core.check.consistency import check_postfix_transport_maps


def test_check_warns_when_transport_maps_missing_routes_source(tmp_path: Path) -> None:
    """When main.cf transport_maps is set but does NOT list a mysql
    source pointing at the routes table, check returns an error finding."""
    main_cf = tmp_path / "main.cf"
    main_cf.write_text(
        "myhostname = test\n"
        "transport_maps = mysql:/etc/postfix/sql-virtual_transport.cf\n"
    )
    findings = check_postfix_transport_maps(main_cf)
    severities = {f.severity for f in findings}
    assert "error" in severities
    assert any("routes" in f.message for f in findings)


def test_check_passes_when_transport_maps_has_routes_first(tmp_path: Path) -> None:
    main_cf = tmp_path / "main.cf"
    main_cf.write_text(
        "myhostname = test\n"
        "transport_maps = mysql:/etc/postfix/sql-routes.cf,"
        " mysql:/etc/postfix/sql-virtual_transport.cf\n"
    )
    findings = check_postfix_transport_maps(main_cf)
    assert all(f.severity == "info" for f in findings)


def test_check_errors_when_main_cf_missing(tmp_path: Path) -> None:
    """Non-existent main.cf → error finding."""
    main_cf = tmp_path / "main.cf"
    findings = check_postfix_transport_maps(main_cf)
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "main.cf" in findings[0].message


def test_check_errors_when_transport_maps_not_set(tmp_path: Path) -> None:
    """main.cf without transport_maps line → error finding."""
    main_cf = tmp_path / "main.cf"
    main_cf.write_text("myhostname = test\nmydestination = localhost\n")
    findings = check_postfix_transport_maps(main_cf)
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "transport_maps" in findings[0].message


def test_check_errors_when_fewer_than_two_sources(tmp_path: Path) -> None:
    """transport_maps with only one source → error."""
    main_cf = tmp_path / "main.cf"
    main_cf.write_text(
        "transport_maps = mysql:/etc/postfix/sql-routes.cf\n"
    )
    findings = check_postfix_transport_maps(main_cf)
    assert len(findings) >= 1
    assert any(f.severity == "error" for f in findings)


def test_check_errors_when_routes_not_first(tmp_path: Path) -> None:
    """transport_maps with virtual_transport first, routes second → error."""
    main_cf = tmp_path / "main.cf"
    main_cf.write_text(
        "transport_maps = mysql:/etc/postfix/sql-virtual_transport.cf,"
        " mysql:/etc/postfix/sql-routes.cf\n"
    )
    findings = check_postfix_transport_maps(main_cf)
    assert any(f.severity == "error" for f in findings)
    assert any("routes" in f.message for f in findings)


def test_check_errors_when_sources_not_mysql(tmp_path: Path) -> None:
    """transport_maps using non-mysql:// sources → error."""
    main_cf = tmp_path / "main.cf"
    main_cf.write_text(
        "transport_maps = hash:/etc/postfix/routes, hash:/etc/postfix/transport\n"
    )
    findings = check_postfix_transport_maps(main_cf)
    assert any(f.severity == "error" for f in findings)
