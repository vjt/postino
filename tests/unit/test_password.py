import pytest
from pydantic import SecretStr

from postino_core.enums import PasswordScheme
from postino_core.errors import ConfigError
from postino_core.password import hash_password, verify_password


def test_hash_includes_scheme_prefix_bcrypt() -> None:
    h = hash_password(SecretStr("hunter2"), PasswordScheme.BCRYPT)
    assert h.startswith("{BLF-CRYPT}")


def test_hash_includes_scheme_prefix_md5() -> None:
    h = hash_password(SecretStr("hunter2"), PasswordScheme.MD5_CRYPT)
    assert h.startswith("{MD5-CRYPT}")


def test_hash_includes_scheme_prefix_sha512() -> None:
    h = hash_password(SecretStr("hunter2"), PasswordScheme.SHA512_CRYPT)
    assert h.startswith("{SHA512-CRYPT}")


def test_verify_roundtrip_bcrypt() -> None:
    h = hash_password(SecretStr("hunter2"), PasswordScheme.BCRYPT)
    assert verify_password(SecretStr("hunter2"), h) is True
    assert verify_password(SecretStr("wrong"), h) is False


def test_verify_roundtrip_md5() -> None:
    h = hash_password(SecretStr("hunter2"), PasswordScheme.MD5_CRYPT)
    assert verify_password(SecretStr("hunter2"), h) is True
    assert verify_password(SecretStr("wrong"), h) is False


def test_verify_unknown_scheme_raises() -> None:
    with pytest.raises(ConfigError):
        verify_password(SecretStr("x"), "{UNKNOWN-SCHEME}garbage")


def test_verify_no_scheme_prefix_raises() -> None:
    with pytest.raises(ConfigError):
        verify_password(SecretStr("x"), "no-prefix-here")
