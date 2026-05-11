"""PostinoSettings load-order regression tests.

Precedence (low to high, highest wins):
  defaults < system toml < user toml < environment

`extra="forbid"` rejects keys outside the model's declared fields,
both in toml and via env. Subclasses below redirect the toml-file
paths to a tmpdir so we can stage system/user files per test
without touching `/usr/local/etc` or `~/.config`.
"""

from __future__ import annotations

import os
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
    # pydantic-settings 2.x emits a UserWarning at class definition
    # when `toml_file` is set but no stock TomlConfigSettingsSource is
    # in `settings_customise_sources` — we use the custom
    # _PostinoTomlSource (subtable-stripping), so the warning is
    # cosmetic; ignore it for the test scope.
    import warnings

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Config key `toml_file` is set")

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


def test_postinod_subtable_does_not_break_postino_settings(
    tmp_path: Path, isolated_env: pytest.MonkeyPatch
) -> None:
    """A ``[postinod]`` subtable in postino.toml must NOT trip
    ``extra="forbid"`` on PostinoSettings — the two settings classes
    share one file. This is the contract the README's "single config
    file, two tables" deployment pattern relies on."""
    sys_toml, user_toml = _tomls(tmp_path)
    sys_toml.write_text(
        _BASE_TOML
        + '\n[postinod]\nlisten = "127.0.0.1:8080"\nscim_issuer = "https://idp.test"\n'
        + 'scim_audience = "postinod"\n'
    )
    settings = _make_settings_class(sys_toml, user_toml)()  # type: ignore[call-arg]  # WHY: pydantic-settings hydrates fields from toml/env; pyright still sees BaseSettings's positional signature.
    assert settings.vmail_uid == 999


def test_mlmmj_half_set_uid_gid_is_rejected(
    tmp_path: Path, isolated_env: pytest.MonkeyPatch
) -> None:
    """Setting one of mlmmj_uid / mlmmj_gid to a real number while the
    other stays at the -1 sentinel silently disables the chown — surfaces
    as bounced mail with no spool error. Fail-fast at config load."""
    from postino_core.errors import ConfigError

    sys_toml, user_toml = _tomls(tmp_path)
    sys_toml.write_text(
        _BASE_TOML
        + f'mlmmj_spool_dir = "{tmp_path / "spool"}"\n'
        + "mlmmj_uid = 1042\n"  # gid stays at -1
    )
    with pytest.raises(ConfigError, match="mlmmj_uid and mlmmj_gid must agree"):
        _make_settings_class(sys_toml, user_toml)()  # type: ignore[call-arg]  # WHY: pydantic-settings hydrates fields from toml; pyright sees BaseSettings's positional signature.


def test_postino_config_env_var_takes_precedence_over_system_toml(
    tmp_path: Path, isolated_env: pytest.MonkeyPatch
) -> None:
    """``$POSTINO_CONFIG`` must be honoured by the CLI's zero-arg
    ``PostinoSettings()`` construction so CLI + daemon + docker stacks
    converge on one config file. Locks the contract the README
    documents."""
    sys_toml = tmp_path / "system.toml"
    sys_toml.write_text(_BASE_TOML)
    env_toml = tmp_path / "via-env.toml"
    env_toml.write_text(_BASE_TOML.replace("vmail_uid = 999", "vmail_uid = 4242"))

    # Point POSTINO_CONFIG at the env-toml; class-default
    # settings_customise_sources should pick it up ahead of the
    # hardcoded system path. Patch the SYSTEM/USER constants to a
    # non-existent dir so the test does not depend on the real machine.
    isolated_env.setattr("postino_core.config._SYSTEM_TOML", tmp_path / "no-such-system.toml")
    isolated_env.setattr("postino_core.config._USER_TOML", tmp_path / "no-such-user.toml")
    isolated_env.setenv("POSTINO_CONFIG", str(env_toml))
    settings = PostinoSettings()  # type: ignore[call-arg]  # WHY: pydantic-settings populates from sources, not init kwargs
    assert settings.vmail_uid == 4242, "POSTINO_CONFIG TOML was ignored"


def test_mlmmj_settings_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sql_dir = tmp_path / "postfix"
    sql_dir.mkdir()
    (sql_dir / "sql-virtual_mailbox_maps.cf").write_text(
        "user = u\npassword = p\nhosts = h\ndbname = d\n"
    )

    monkeypatch.setenv("POSTINO_IDENTITY_BACKEND", "noauth")
    monkeypatch.setenv("POSTINO_POSTFIX_SQL_DIR", str(sql_dir))
    monkeypatch.setenv("POSTINO_VIRTUAL_MAILBOX_BASE", str(tmp_path / "mail"))
    monkeypatch.setenv("POSTINO_POSTCREATION_HOOK", str(tmp_path / "hook.sh"))
    monkeypatch.setenv("POSTINO_VMAIL_UID", "5000")
    monkeypatch.setenv("POSTINO_VMAIL_GID", "5000")
    monkeypatch.setenv("POSTINO_DEFAULT_PASSWORD_SCHEME", "BLF-CRYPT")
    monkeypatch.setenv("POSTINO_DEFAULT_QUOTA_BYTES", "1073741824")
    monkeypatch.setenv("POSTINO_MLMMJ_SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.setenv("POSTINO_MLMMJ_UID", "1234")
    monkeypatch.setenv("POSTINO_MLMMJ_GID", "5678")

    from postino_core.config import PostinoSettings

    s = PostinoSettings()  # type: ignore[call-arg]  # WHY: pydantic-settings populates from env
    assert s.mlmmj_spool_dir == tmp_path / "spool"
    assert s.mlmmj_uid == 1234
    assert s.mlmmj_gid == 5678


def test_mlmmj_settings_default_to_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sql_dir = tmp_path / "postfix"
    sql_dir.mkdir()
    (sql_dir / "sql-virtual_mailbox_maps.cf").write_text(
        "user = u\npassword = p\nhosts = h\ndbname = d\n"
    )

    monkeypatch.setenv("POSTINO_IDENTITY_BACKEND", "noauth")
    monkeypatch.setenv("POSTINO_POSTFIX_SQL_DIR", str(sql_dir))
    monkeypatch.setenv("POSTINO_VIRTUAL_MAILBOX_BASE", str(tmp_path / "mail"))
    monkeypatch.setenv("POSTINO_POSTCREATION_HOOK", str(tmp_path / "hook.sh"))
    monkeypatch.setenv("POSTINO_VMAIL_UID", "5000")
    monkeypatch.setenv("POSTINO_VMAIL_GID", "5000")
    monkeypatch.setenv("POSTINO_DEFAULT_PASSWORD_SCHEME", "BLF-CRYPT")
    monkeypatch.setenv("POSTINO_DEFAULT_QUOTA_BYTES", "1073741824")
    monkeypatch.delenv("POSTINO_MLMMJ_SPOOL_DIR", raising=False)
    monkeypatch.delenv("POSTINO_MLMMJ_UID", raising=False)
    monkeypatch.delenv("POSTINO_MLMMJ_GID", raising=False)

    from postino_core.config import PostinoSettings

    s = PostinoSettings()  # type: ignore[call-arg]  # WHY: pydantic-settings populates from env
    assert s.mlmmj_spool_dir is None
    assert s.mlmmj_uid == -1
    assert s.mlmmj_gid == -1


@pytest.mark.integration
def test_bundle_wires_mailing_list_when_spool_dir_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """build_services constructs MailingListService when mlmmj_spool_dir is set."""
    spool = tmp_path / "spool"
    spool.mkdir()
    sql_dir = tmp_path / "postfix"
    sql_dir.mkdir()
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    from tests.cli.test_user_cmd import env_for_cli, make_postfix_cf

    make_postfix_cf(db_url, sql_dir)
    env = env_for_cli(db_url, tmp_path / "mail", tmp_path / "hook.sh", sql_dir)
    env["POSTINO_MLMMJ_SPOOL_DIR"] = str(spool)
    # Real uid/gid required by the PostinoSettings validator once a spool
    # dir is set; values don't matter here because the test only asserts
    # the service is wired, not that we chown.
    env["POSTINO_MLMMJ_UID"] = "1042"
    env["POSTINO_MLMMJ_GID"] = "1042"
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    (tmp_path / "mail").mkdir(exist_ok=True)
    (tmp_path / "hook.sh").write_text("#!/bin/sh\nexit 0\n")
    (tmp_path / "hook.sh").chmod(0o755)

    from datetime import UTC, datetime

    from postino_core.config import PostinoSettings
    from postino_core.services.bundle import build_services

    s = PostinoSettings()  # type: ignore[call-arg]  # WHY: pydantic-settings env-driven
    bundle = build_services(s, clock=lambda: datetime.now(UTC), echo=False)
    assert bundle.mailing_list is not None


@pytest.mark.integration
def test_bundle_mailing_list_none_when_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sql_dir = tmp_path / "postfix"
    sql_dir.mkdir()
    db_url = os.environ["POSTINO_TEST_DB_URL"]
    from tests.cli.test_user_cmd import env_for_cli, make_postfix_cf

    make_postfix_cf(db_url, sql_dir)
    env = env_for_cli(db_url, tmp_path / "mail", tmp_path / "hook.sh", sql_dir)
    monkeypatch.delenv("POSTINO_MLMMJ_SPOOL_DIR", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    (tmp_path / "mail").mkdir(exist_ok=True)
    (tmp_path / "hook.sh").write_text("#!/bin/sh\nexit 0\n")
    (tmp_path / "hook.sh").chmod(0o755)

    from datetime import UTC, datetime

    from postino_core.config import PostinoSettings
    from postino_core.services.bundle import build_services

    s = PostinoSettings()  # type: ignore[call-arg]  # WHY: pydantic-settings env-driven
    bundle = build_services(s, clock=lambda: datetime.now(UTC), echo=False)
    assert bundle.mailing_list is None
