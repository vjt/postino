"""PostinoSettings load-order regression tests.

Precedence (low to high, highest wins):
  defaults < system toml < user toml < environment

`extra="forbid"` rejects keys outside the model's declared fields,
both in toml and via env. Subclasses below redirect the toml-file
paths to a tmpdir so we can stage system/user files per test
without touching `/usr/local/etc` or `~/.config`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from postino_core.config import PostinoSettings


def _tomls(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "system.toml", tmp_path / "user.toml"


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Strip POSTINO_* env vars so each test starts from a known baseline."""
    for key in list(__import__("os").environ):
        if key.startswith("POSTINO_"):
            monkeypatch.delenv(key, raising=False)
    yield monkeypatch


def _make_settings_class(sys_toml: Path, user_toml: Path) -> type[PostinoSettings]:
    class _Scoped(PostinoSettings):
        model_config = SettingsConfigDict(
            env_prefix="POSTINO_",
            env_nested_delimiter="__",
            extra="forbid",
            toml_file=[str(sys_toml), str(user_toml)],
        )

    return _Scoped


_BASE_TOML = """
identity_backend = "local"
postfix_sql_dir = "/etc/postfix"
virtual_mailbox_base = "/var/spool/vmail"
postcreation_hook = "/usr/local/sbin/postcreation"
vmail_uid = 999
vmail_gid = 999
default_password_scheme = "BLF-CRYPT"
default_quota_bytes = 5368709120
"""


def test_loads_when_only_system_toml_present(
    tmp_path: Path, isolated_env: pytest.MonkeyPatch
) -> None:
    sys_toml, user_toml = _tomls(tmp_path)
    sys_toml.write_text(_BASE_TOML)
    settings = _make_settings_class(sys_toml, user_toml)()  # type: ignore[call-arg]  # WHY: pydantic-settings hydrates fields from toml/env; pyright still sees BaseSettings's positional signature.
    assert settings.vmail_uid == 999
    assert settings.default_quota_bytes == 5368709120


def test_user_toml_overrides_system_toml(tmp_path: Path, isolated_env: pytest.MonkeyPatch) -> None:
    sys_toml, user_toml = _tomls(tmp_path)
    sys_toml.write_text(_BASE_TOML)
    user_toml.write_text("vmail_uid = 1500\nvmail_gid = 1500\n")
    settings = _make_settings_class(sys_toml, user_toml)()  # type: ignore[call-arg]  # WHY: pydantic-settings hydrates fields from toml/env; pyright still sees BaseSettings's positional signature.
    assert settings.vmail_uid == 1500
    assert settings.vmail_gid == 1500
    # System-toml fields the user did not override stay intact.
    assert settings.default_quota_bytes == 5368709120


def test_env_overrides_both_toml_files(tmp_path: Path, isolated_env: pytest.MonkeyPatch) -> None:
    sys_toml, user_toml = _tomls(tmp_path)
    sys_toml.write_text(_BASE_TOML)
    user_toml.write_text("vmail_uid = 1500\n")
    isolated_env.setenv("POSTINO_VMAIL_UID", "2000")
    settings = _make_settings_class(sys_toml, user_toml)()  # type: ignore[call-arg]  # WHY: pydantic-settings hydrates fields from toml/env; pyright still sees BaseSettings's positional signature.
    assert settings.vmail_uid == 2000


def test_defaults_fill_when_field_unset(tmp_path: Path, isolated_env: pytest.MonkeyPatch) -> None:
    """Fields with defaults (postcreation_hook_timeout, lmtp_destination)
    fall back when neither toml nor env supplies a value."""
    sys_toml, user_toml = _tomls(tmp_path)
    sys_toml.write_text(_BASE_TOML)
    settings = _make_settings_class(sys_toml, user_toml)()  # type: ignore[call-arg]  # WHY: pydantic-settings hydrates fields from toml/env; pyright still sees BaseSettings's positional signature.
    assert settings.postcreation_hook_timeout == 30.0
    assert settings.lmtp_destination == "unix:private/dovecot-lmtp"


def test_extra_forbid_rejects_unknown_toml_key(
    tmp_path: Path, isolated_env: pytest.MonkeyPatch
) -> None:
    """`extra=forbid` is the contract guarding typos in the TOML —
    pydantic-settings only feeds declared fields into the model from
    env, so the env path cannot leak unknown keys; the TOML path can,
    and this test locks in the rejection."""
    sys_toml, user_toml = _tomls(tmp_path)
    sys_toml.write_text(_BASE_TOML + 'unknown_field = "rogue"\n')
    with pytest.raises(ValidationError):
        _make_settings_class(sys_toml, user_toml)()  # type: ignore[call-arg]  # WHY: pydantic-settings hydrates from toml; pyright sees BaseSettings's positional signature.
