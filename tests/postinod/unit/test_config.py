"""PostinodSettings — TOML scope, env override, env-only secret reader."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from pydantic import ValidationError

from postino_core.errors import ConfigError
from postinod.config import (
    HMAC_MIN_BYTES,
    load_postinod_settings,
    read_zitadel_hmac_secrets,
    read_zitadel_replay_window_sec,
)


def _write_toml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "postino.toml"
    p.write_text(dedent(body), encoding="utf-8")
    return p


def _good_secret(byte_len: int = HMAC_MIN_BYTES) -> str:
    return "a" * byte_len


def test_loads_postinod_table_only(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
        identity_backend = "noauth"
        postfix_sql_dir = "/etc/postfix"

        [postinod]
        listen = "127.0.0.1:9000"
        log_level = "INFO"
        zitadel_issuer = "https://zitadel.example.org"
        scim_issuer = "https://idp.example.org"
        scim_audience = "postinod"
        """,
    )
    s = load_postinod_settings(toml)
    assert s.listen == "127.0.0.1:9000"
    assert s.scim_audience == "postinod"


def test_settings_has_no_hmac_field(tmp_path: Path) -> None:
    """Secrets MUST NOT live in PostinodSettings — env-only reader path."""
    toml = _write_toml(
        tmp_path,
        """
        [postinod]
        zitadel_issuer = "https://zitadel.example.org"
        scim_issuer = "https://idp.example.org"
        scim_audience = "postinod"
        """,
    )
    s = load_postinod_settings(toml)
    assert not hasattr(s, "zitadel_hmac_secret")


def test_toml_hmac_secret_is_forbidden(tmp_path: Path) -> None:
    """A stray `zitadel_hmac_secret` in TOML must hard-fail, not silently load."""
    toml = _write_toml(
        tmp_path,
        """
        [postinod]
        zitadel_issuer = "https://zitadel.example.org"
        scim_issuer = "https://idp.example.org"
        scim_audience = "postinod"
        zitadel_hmac_secret = "some-leaked-value"
        """,
    )
    with pytest.raises(ValidationError):
        load_postinod_settings(toml)


def test_extra_keys_forbidden(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
        [postinod]
        listen = "0.0.0.0:8443"
        zitadel_issuer = "https://zitadel.example.org"
        scim_issuer = "https://idp.example.org"
        scim_audience = "postinod"
        unknown_key = "boom"
        """,
    )
    with pytest.raises(ValidationError):
        load_postinod_settings(toml)


def test_env_overrides_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTINOD_LISTEN", "0.0.0.0:7000")
    toml = _write_toml(
        tmp_path,
        """
        [postinod]
        listen = "0.0.0.0:8443"
        zitadel_issuer = "https://zitadel.example.org"
        scim_issuer = "https://idp.example.org"
        scim_audience = "postinod"
        """,
    )
    s = load_postinod_settings(toml)
    assert s.listen == "0.0.0.0:7000"


def test_default_jwks_refresh_seconds(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
        [postinod]
        zitadel_issuer = "https://zitadel.example.org"
        scim_issuer = "https://idp.example.org"
        scim_audience = "postinod"
        """,
    )
    s = load_postinod_settings(toml)
    assert s.scim_jwks_refresh_seconds == 3600


def test_malformed_toml_raises_with_path(tmp_path: Path) -> None:
    p = tmp_path / "postino.toml"
    p.write_text("this = is = not valid toml\n[unclosed", encoding="utf-8")
    with pytest.raises(RuntimeError) as exc:
        load_postinod_settings(p)
    assert str(p) in str(exc.value)


def test_read_hmac_secrets_single(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTINOD_ZITADEL_HMAC_SECRET", _good_secret())
    secrets = read_zitadel_hmac_secrets()
    assert len(secrets) == 1
    assert secrets[0] == _good_secret().encode()


def test_read_hmac_secrets_rotation_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = f"{_good_secret()},{_good_secret(40)}"
    monkeypatch.setenv("POSTINOD_ZITADEL_HMAC_SECRET", raw)
    secrets = read_zitadel_hmac_secrets()
    assert len(secrets) == 2
    assert all(len(s) >= HMAC_MIN_BYTES for s in secrets)


def test_read_hmac_secrets_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = f"  {_good_secret()} ,\t{_good_secret(40)}  "
    monkeypatch.setenv("POSTINOD_ZITADEL_HMAC_SECRET", raw)
    secrets = read_zitadel_hmac_secrets()
    assert len(secrets) == 2


def test_read_hmac_secrets_missing_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTINOD_ZITADEL_HMAC_SECRET", raising=False)
    with pytest.raises(ConfigError) as exc:
        read_zitadel_hmac_secrets()
    assert "POSTINOD_ZITADEL_HMAC_SECRET" in str(exc.value)
    assert "openssl rand" in str(exc.value)


def test_read_hmac_secrets_empty_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTINOD_ZITADEL_HMAC_SECRET", "   ")
    with pytest.raises(ConfigError):
        read_zitadel_hmac_secrets()


def test_read_hmac_secrets_short_entry_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTINOD_ZITADEL_HMAC_SECRET", "tooshort")
    with pytest.raises(ConfigError) as exc:
        read_zitadel_hmac_secrets()
    assert "32" in str(exc.value)


def test_read_hmac_secrets_short_in_rotation_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "POSTINOD_ZITADEL_HMAC_SECRET",
        f"{_good_secret()},tooshort",
    )
    with pytest.raises(ConfigError) as exc:
        read_zitadel_hmac_secrets()
    assert "#2" in str(exc.value)


def test_read_replay_window_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTINOD_ZITADEL_REPLAY_WINDOW_SEC", raising=False)
    assert read_zitadel_replay_window_sec() == 300


def test_read_replay_window_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTINOD_ZITADEL_REPLAY_WINDOW_SEC", "60")
    assert read_zitadel_replay_window_sec() == 60


def test_read_replay_window_invalid_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTINOD_ZITADEL_REPLAY_WINDOW_SEC", "not-an-int")
    with pytest.raises(ConfigError):
        read_zitadel_replay_window_sec()


def test_read_replay_window_non_positive_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTINOD_ZITADEL_REPLAY_WINDOW_SEC", "0")
    with pytest.raises(ConfigError):
        read_zitadel_replay_window_sec()
