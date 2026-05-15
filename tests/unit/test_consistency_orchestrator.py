"""Unit test confirming run_consistency_check invokes all 4 v0.10 mlmmj
validators when mlmmj_spool_dir is configured.

Uses unittest.mock.patch to intercept the 4 new validators so the test
stays self-contained (no real DB / filesystem required).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from postino_core.check.consistency import Finding, run_consistency_check
from postino_core.config import PostinoSettings
from postino_core.enums import IdentityBackend, PasswordScheme


@pytest.fixture
def stub_settings(tmp_path: Path) -> PostinoSettings:
    """Minimal PostinoSettings with mlmmj_spool_dir set."""
    return PostinoSettings(
        identity_backend=IdentityBackend.LOCAL,
        postfix_sql_dir=tmp_path / "postfix",
        virtual_mailbox_base=tmp_path / "mail",
        postcreation_hook=Path("/usr/local/sbin/postfixadmin-mailbox-postcreation.sh"),
        vmail_uid=1006,
        vmail_gid=1006,
        default_password_scheme=PasswordScheme.BCRYPT,
        default_quota_bytes=1024**3,
        mlmmj_spool_dir=tmp_path / "mlmmj",
    )


_OK = Finding(name="stub", severity="info", message="ok")

_PATCH_BASE = "postino_core.check.consistency"


def test_run_consistency_check_calls_all_four_mlmmj_validators(
    stub_settings: PostinoSettings,
) -> None:
    """All 4 v0.10 mlmmj validators must be called when mlmmj_spool_dir is set."""
    stub_engine = MagicMock()
    stub_metadata = MagicMock()

    # Core checks that run unconditionally need stubs too so the
    # orchestrator doesn't crash on the missing DB / filesystem.
    with (
        patch(f"{_PATCH_BASE}._check_db_reachable", return_value=_OK),
        patch(f"{_PATCH_BASE}._check_required_tables", return_value=_OK),
        patch(f"{_PATCH_BASE}._check_mailbox_base", return_value=_OK),
        patch(f"{_PATCH_BASE}._check_postcreation_hook", return_value=_OK),
        patch(f"{_PATCH_BASE}._check_postcreation_hook_syntax", return_value=_OK),
        patch(f"{_PATCH_BASE}._check_vmail_identity", return_value=[_OK, _OK]),
        patch(f"{_PATCH_BASE}._check_db_grants", return_value=[_OK]),
        patch(f"{_PATCH_BASE}._check_postfix_sql_cfs", return_value=[_OK]),
        patch(f"{_PATCH_BASE}.check_postfix_transport_maps", return_value=[_OK]) as mock_transport,
        patch(f"{_PATCH_BASE}.check_recipient_delimiter", return_value=[_OK]) as mock_delimiter,
        patch(f"{_PATCH_BASE}.check_master_cf_mlmmj_pipes", return_value=[_OK]) as mock_pipes,
        patch(f"{_PATCH_BASE}.check_owner_aliases_for_routes", return_value=[_OK]) as mock_owner,
    ):
        result = run_consistency_check(
            settings=stub_settings,
            engine=stub_engine,
            metadata=stub_metadata,
        )

    assert mock_transport.called, "check_postfix_transport_maps was not invoked"
    assert mock_delimiter.called, "check_recipient_delimiter was not invoked"
    assert mock_pipes.called, "check_master_cf_mlmmj_pipes was not invoked"
    assert mock_owner.called, "check_owner_aliases_for_routes was not invoked"
    assert result.ok  # all stubs return info findings → no errors
