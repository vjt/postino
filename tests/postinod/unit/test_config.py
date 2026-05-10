"""PostinodSettings — TOML scope, env override, fail-fast validation."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from pydantic import ValidationError

from postinod.config import load_postinod_settings


def _write_toml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "postino.toml"
    p.write_text(dedent(body), encoding="utf-8")
    return p


def test_loads_postinod_table_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTINOD_ZITADEL_HMAC_SECRET", "topsecret")
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
    assert s.zitadel_hmac_secret.get_secret_value() == "topsecret"
    assert s.scim_audience == "postinod"


def test_missing_hmac_secret_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTINOD_ZITADEL_HMAC_SECRET", raising=False)
    toml = _write_toml(
        tmp_path,
        """
        [postinod]
        zitadel_issuer = "https://zitadel.example.org"
        scim_issuer = "https://idp.example.org"
        scim_audience = "postinod"
        """,
    )
    with pytest.raises(ValidationError) as exc:
        load_postinod_settings(toml)
    assert "POSTINOD_ZITADEL_HMAC_SECRET" in str(exc.value)


def test_extra_keys_forbidden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTINOD_ZITADEL_HMAC_SECRET", "x")
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
    monkeypatch.setenv("POSTINOD_ZITADEL_HMAC_SECRET", "x")
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


def test_default_jwks_refresh_seconds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTINOD_ZITADEL_HMAC_SECRET", "x")
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


def test_malformed_toml_raises_with_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTINOD_ZITADEL_HMAC_SECRET", "x")
    p = tmp_path / "postino.toml"
    p.write_text("this = is = not valid toml\n[unclosed", encoding="utf-8")
    with pytest.raises(RuntimeError) as exc:
        load_postinod_settings(p)
    assert str(p) in str(exc.value)
