from pathlib import Path

import pytest
from pydantic import SecretStr

from postino_core.config import (
    PostfixSqlCredentials,
    PostinoSettings,
    parse_postfix_sql_cf,
)
from postino_core.enums import IdentityBackend, PasswordScheme
from postino_core.errors import ConfigError

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_sql-virtual_mailbox_maps.cf"


def test_parse_postfix_cf() -> None:
    creds = parse_postfix_sql_cf(FIXTURE)
    assert creds == PostfixSqlCredentials(
        host="localhost",
        user="postfix",
        password=SecretStr("sekret"),
        dbname="postfix",
    )


def test_parse_postfix_cf_missing_field(tmp_path: Path) -> None:
    bad = tmp_path / "bad.cf"
    bad.write_text("hosts = localhost\nuser = postfix\n")
    with pytest.raises(ConfigError):
        parse_postfix_sql_cf(bad)


def test_settings_dburl() -> None:
    creds = PostfixSqlCredentials(
        host="localhost",
        user="postfix",
        password=SecretStr("sekret"),
        dbname="postfix",
    )
    assert creds.sqlalchemy_url() == "mysql+pymysql://postfix:sekret@localhost/postfix"


def test_credentials_password_redacted_in_repr_and_str() -> None:
    creds = PostfixSqlCredentials(
        host="localhost",
        user="postfix",
        password=SecretStr("hunter2-leak-probe"),
        dbname="postfix",
    )
    assert "hunter2-leak-probe" not in repr(creds)
    assert "hunter2-leak-probe" not in str(creds)
    # Other field values must still be inspectable for debugging.
    assert "localhost" in repr(creds)
    assert "postfix" in repr(creds)


def test_credentials_password_redacted_in_pydantic_dump() -> None:
    creds = PostfixSqlCredentials(
        host="localhost",
        user="postfix",
        password=SecretStr("hunter2-leak-probe"),
        dbname="postfix",
    )
    # model_dump (no mode kwarg) keeps SecretStr instances; repr-redact
    # still applies. JSON-dump masks via SecretStr.__str__ default.
    dumped = creds.model_dump()
    assert isinstance(dumped["password"], SecretStr)
    assert "hunter2-leak-probe" not in repr(dumped)


def test_settings_defaults_local_backend() -> None:
    s = PostinoSettings(
        identity_backend=IdentityBackend.LOCAL,
        postfix_sql_dir=Path("/tmp"),
        virtual_mailbox_base=Path("/srv/mail"),
        postcreation_hook=Path("/usr/local/sbin/postfixadmin-mailbox-postcreation.sh"),
        vmail_uid=1006,
        vmail_gid=1006,
        default_password_scheme=PasswordScheme.BCRYPT,
        default_quota_bytes=1024**3,
    )
    assert s.identity_backend == IdentityBackend.LOCAL


def test_settings_postcreation_hook_timeout_default() -> None:
    s = PostinoSettings(
        identity_backend=IdentityBackend.LOCAL,
        postfix_sql_dir=Path("/tmp"),
        virtual_mailbox_base=Path("/srv/mail"),
        postcreation_hook=Path("/x"),
        vmail_uid=1006,
        vmail_gid=1006,
        default_password_scheme=PasswordScheme.BCRYPT,
        default_quota_bytes=1024**3,
    )
    assert s.postcreation_hook_timeout == 30.0


def test_noauth_backend_accepted() -> None:
    s = PostinoSettings(
        identity_backend=IdentityBackend.NOAUTH,
        postfix_sql_dir=Path("/tmp"),
        virtual_mailbox_base=Path("/srv/mail"),
        postcreation_hook=Path("/x"),
        vmail_uid=1006,
        vmail_gid=1006,
        default_password_scheme=PasswordScheme.BCRYPT,
        default_quota_bytes=1024**3,
    )
    assert s.identity_backend is IdentityBackend.NOAUTH


def test_unknown_backend_string_rejected() -> None:
    """Unknown identity_backend string fails at the enum boundary, not the validator."""
    with pytest.raises(ValueError):
        PostinoSettings(
            identity_backend="zitadel",  # type: ignore[arg-type]  # WHY: deliberately exercising the enum coercion path with an invalid value.
            postfix_sql_dir=Path("/tmp"),
            virtual_mailbox_base=Path("/srv/mail"),
            postcreation_hook=Path("/x"),
            vmail_uid=1006,
            vmail_gid=1006,
            default_password_scheme=PasswordScheme.BCRYPT,
            default_quota_bytes=1024**3,
        )
