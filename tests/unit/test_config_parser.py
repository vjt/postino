from pathlib import Path

import pytest

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
        password="sekret",
        dbname="postfix",
    )


def test_parse_postfix_cf_missing_field(tmp_path: Path) -> None:
    bad = tmp_path / "bad.cf"
    bad.write_text("hosts = localhost\nuser = postfix\n")
    with pytest.raises(ConfigError):
        parse_postfix_sql_cf(bad)


def test_settings_dburl() -> None:
    creds = PostfixSqlCredentials(
        host="localhost", user="postfix", password="sekret", dbname="postfix"
    )
    assert creds.sqlalchemy_url() == "mysql+pymysql://postfix:sekret@localhost/postfix"


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


def test_zitadel_backend_rejected_in_mvp() -> None:
    with pytest.raises(ConfigError):
        PostinoSettings(
            identity_backend=IdentityBackend.ZITADEL,
            postfix_sql_dir=Path("/tmp"),
            virtual_mailbox_base=Path("/srv/mail"),
            postcreation_hook=Path("/x"),
            vmail_uid=1006,
            vmail_gid=1006,
            default_password_scheme=PasswordScheme.BCRYPT,
            default_quota_bytes=1024**3,
        )
