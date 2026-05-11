"""Unit tests for the dovecot passdb chain consistency check.

Covers the _check_dovecot_passdb_chain function across NOAUTH, HYBRID,
and LOCAL backends. The check must verify that when identity_backend is
NOAUTH or HYBRID, dovecot has at least one non-SQL passdb block for
the external IdP passdb to resolve sentinel rows.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from postino_core.check.consistency import (
    _check_dovecot_passdb_chain,  # pyright: ignore[reportPrivateUsage]  # WHY: regression guard for the dovecot passdb chain check; module-private by design.
)
from postino_core.config import PostinoSettings
from postino_core.enums import IdentityBackend, PasswordScheme


@pytest.fixture
def base_settings() -> PostinoSettings:
    """Base PostinoSettings for all tests."""
    return PostinoSettings(
        identity_backend=IdentityBackend.LOCAL,
        postfix_sql_dir=Path("/tmp"),
        virtual_mailbox_base=Path("/srv/mail"),
        postcreation_hook=Path("/usr/local/sbin/postfixadmin-mailbox-postcreation.sh"),
        vmail_uid=1006,
        vmail_gid=1006,
        default_password_scheme=PasswordScheme.BCRYPT,
        default_quota_bytes=1024**3,
    )


def test_local_backend_skipped(base_settings: PostinoSettings) -> None:
    """LOCAL backend is never checked — returns empty list."""
    s = base_settings.model_copy(update={"identity_backend": IdentityBackend.LOCAL})
    result = _check_dovecot_passdb_chain(s)
    assert result == []


def test_noauth_backend_only_sql_passdb_error(
    base_settings: PostinoSettings, tmp_path: Path
) -> None:
    """NOAUTH + only sql passdb → error."""
    s = base_settings.model_copy(update={"identity_backend": IdentityBackend.NOAUTH})

    auth_conf = tmp_path / "auth-sql.conf.ext"
    auth_conf.write_text(
        """
        passdb {
          driver = sql
          args = /etc/dovecot/dovecot-sql.conf.ext
        }
        """
    )

    with patch(
        "postino_core.check.consistency._DOVECOT_CONF_DIRS",
        [tmp_path],
    ):
        result = _check_dovecot_passdb_chain(s)

    assert len(result) == 1
    assert result[0].severity == "error"
    assert "dovecot_passdb_chain" in result[0].name
    assert "driver=sql" in result[0].message


def test_noauth_backend_with_non_sql_passdb_ok(
    base_settings: PostinoSettings, tmp_path: Path
) -> None:
    """NOAUTH + non-sql passdb present → ok."""
    s = base_settings.model_copy(update={"identity_backend": IdentityBackend.NOAUTH})

    auth_conf = tmp_path / "auth-sql.conf.ext"
    auth_conf.write_text(
        """
        passdb {
          driver = sql
          args = /etc/dovecot/dovecot-sql.conf.ext
        }

        passdb {
          driver = ldap
          args = /etc/dovecot/dovecot-ldap.conf.ext
        }
        """
    )

    with patch(
        "postino_core.check.consistency._DOVECOT_CONF_DIRS",
        [tmp_path],
    ):
        result = _check_dovecot_passdb_chain(s)

    assert len(result) == 1
    assert result[0].severity == "info"
    assert "ldap" in result[0].message


def test_hybrid_backend_only_sql_passdb_error(
    base_settings: PostinoSettings, tmp_path: Path
) -> None:
    """HYBRID + only sql passdb → error (same as NOAUTH)."""
    s = base_settings.model_copy(update={"identity_backend": IdentityBackend.HYBRID})

    auth_conf = tmp_path / "auth-sql.conf.ext"
    auth_conf.write_text(
        """
        passdb {
          driver = sql
          args = /etc/dovecot/dovecot-sql.conf.ext
        }
        """
    )

    with patch(
        "postino_core.check.consistency._DOVECOT_CONF_DIRS",
        [tmp_path],
    ):
        result = _check_dovecot_passdb_chain(s)

    assert len(result) == 1
    assert result[0].severity == "error"
    assert "dovecot_passdb_chain" in result[0].name
    assert "driver=sql" in result[0].message


def test_hybrid_backend_with_non_sql_passdb_ok(
    base_settings: PostinoSettings, tmp_path: Path
) -> None:
    """HYBRID + non-sql passdb present → ok."""
    s = base_settings.model_copy(update={"identity_backend": IdentityBackend.HYBRID})

    auth_conf = tmp_path / "auth-sql.conf.ext"
    auth_conf.write_text(
        """
        passdb {
          driver = sql
          args = /etc/dovecot/dovecot-sql.conf.ext
        }

        passdb {
          driver = passwd-file
          args = scheme=PLAIN /etc/dovecot/users
        }
        """
    )

    with patch(
        "postino_core.check.consistency._DOVECOT_CONF_DIRS",
        [tmp_path],
    ):
        result = _check_dovecot_passdb_chain(s)

    assert len(result) == 1
    assert result[0].severity == "info"
    assert "passwd-file" in result[0].message


def test_hybrid_backend_multiple_non_sql_passdbs(
    base_settings: PostinoSettings, tmp_path: Path
) -> None:
    """HYBRID + multiple non-sql passdbs → ok with all listed."""
    s = base_settings.model_copy(update={"identity_backend": IdentityBackend.HYBRID})

    auth_conf = tmp_path / "auth-sql.conf.ext"
    auth_conf.write_text(
        """
        passdb {
          driver = sql
        }
        passdb {
          driver = ldap
        }
        passdb {
          driver = pam
        }
        """
    )

    with patch(
        "postino_core.check.consistency._DOVECOT_CONF_DIRS",
        [tmp_path],
    ):
        result = _check_dovecot_passdb_chain(s)

    assert len(result) == 1
    assert result[0].severity == "info"
    # Both non-sql drivers should be listed
    assert "ldap" in result[0].message
    assert "pam" in result[0].message


def test_noauth_backend_no_auth_files_warn(base_settings: PostinoSettings, tmp_path: Path) -> None:
    """NOAUTH + no auth-*.conf.ext files found → warning."""
    s = base_settings.model_copy(update={"identity_backend": IdentityBackend.NOAUTH})

    with patch(
        "postino_core.check.consistency._DOVECOT_CONF_DIRS",
        [tmp_path],
    ):
        result = _check_dovecot_passdb_chain(s)

    assert len(result) == 1
    assert result[0].severity == "warn"
    assert "cannot verify" in result[0].message


def test_hybrid_backend_no_auth_files_warn(base_settings: PostinoSettings, tmp_path: Path) -> None:
    """HYBRID + no auth-*.conf.ext files found → warning."""
    s = base_settings.model_copy(update={"identity_backend": IdentityBackend.HYBRID})

    with patch(
        "postino_core.check.consistency._DOVECOT_CONF_DIRS",
        [tmp_path],
    ):
        result = _check_dovecot_passdb_chain(s)

    assert len(result) == 1
    assert result[0].severity == "warn"
    assert "cannot verify" in result[0].message
