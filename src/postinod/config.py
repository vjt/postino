"""PostinodSettings — daemon-only configuration.

Reads the [postinod] table from the same TOML file PostinoSettings reads
(/usr/local/etc/postino/postino.toml). Daemon-only keys cluster here so
operators see one config file, not two.

Env override prefix: POSTINOD_ (single underscore, no nesting).

Secrets are env-only: the HMAC secret for the Zitadel webhook NEVER lives
in TOML — `read_zitadel_hmac_secrets()` reads `POSTINOD_ZITADEL_HMAC_SECRET`
directly, validates entropy and parses comma-separated rotation overlap, and
raises `ConfigError` on anything missing/too-short. The replay window for
incoming events is similarly env-only (`POSTINOD_ZITADEL_REPLAY_WINDOW_SEC`).
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from tomllib import TOMLDecodeError

from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from postino_core.errors import ConfigError

HMAC_MIN_BYTES = 32
"""Minimum acceptable HMAC secret length, in *raw* bytes (post hex-decode).

32 raw bytes = 256 bits, matching `openssl rand -hex 32`'s output. A
secret supplied as hex characters is decoded before this check, so an
operator who pastes `openssl rand -hex 16` (16 raw bytes / 32 hex
chars) is rejected — fixes A4-A4.6 (prior check compared character
count, not byte count, accepting half the documented entropy)."""

DEFAULT_REPLAY_WINDOW_SEC = 300
"""Default replay window: reject Zitadel events whose `created_at` is
more than this many seconds away from the local clock."""


class _PostinodTomlSource(PydanticBaseSettingsSource):
    """Reads the [postinod] subtable from a postino.toml file."""

    def __init__(self, settings_cls: type[BaseSettings], toml_path: Path) -> None:
        super().__init__(settings_cls)
        self._toml_path = toml_path

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[object, str, bool]:
        raise NotImplementedError  # __call__ is the public path

    def __call__(self) -> dict[str, object]:
        if not self._toml_path.is_file():
            return {}
        try:
            with self._toml_path.open("rb") as f:
                raw = tomllib.load(f)  # dict[str, Any] from tomllib stubs
        except TOMLDecodeError as e:
            raise RuntimeError(f"failed to parse TOML config at {self._toml_path}: {e}") from e
        section = raw.get("postinod", {})
        if not isinstance(section, dict):
            return {}
        return section  # type: ignore[return-value]  # WHY: tomllib stubs type values as Any; isinstance guard confirms dict shape but pyright can't narrow the value type further


class PostinodSettings(BaseSettings):
    """Daemon configuration; constructed via `load_postinod_settings`.

    Secrets (HMAC, future tokens) are NOT modelled here — they are
    read separately via `read_zitadel_hmac_secrets()` from env only, so a
    TOML defaulting accident cannot leak them into config storage.
    """

    model_config = SettingsConfigDict(
        env_prefix="POSTINOD_",
        env_nested_delimiter=None,
        extra="forbid",
        frozen=True,
    )

    # Network
    listen: str = "0.0.0.0:8443"
    log_level: str = "INFO"

    # Zitadel surface
    zitadel_issuer: str

    # SCIM surface
    scim_issuer: str
    scim_audience: str
    scim_jwks_refresh_seconds: int = 3600
    # Maximum accepted token age, in seconds. Caps revocation latency
    # for the SCIM bearer JWT independently of the IdP's `exp` claim
    # (A4-A4.5). Default 1h; operators with shorter rotation windows
    # can lower it.
    scim_max_token_age_seconds: int = 3600


def load_postinod_settings(toml_path: Path) -> PostinodSettings:
    """Build PostinodSettings from `[postinod]` in toml_path + env overrides."""

    class _PostinodSettingsImpl(PostinodSettings):
        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings: PydanticBaseSettingsSource,
        ) -> tuple[PydanticBaseSettingsSource, ...]:
            return (
                init_settings,
                env_settings,
                _PostinodTomlSource(settings_cls, toml_path),
            )

    return _PostinodSettingsImpl()  # type: ignore[call-arg]  # WHY: pydantic-settings populates from sources, not init kwargs


def read_zitadel_hmac_secrets(
    env: Mapping[str, str] | None = None,
) -> tuple[bytes, ...]:
    """Read `POSTINOD_ZITADEL_HMAC_SECRET` from env as a non-empty tuple of secrets.

    Comma-separated values are supported for rotation overlap: callers
    can publish two secrets to Zitadel during a key roll, accept both
    here, drop the old one once Zitadel cuts over.

    Every secret is validated `len >= HMAC_MIN_BYTES`. Failures raise
    `ConfigError` with the openssl-rand-hex-32 hint.
    """
    raw = (env or os.environ).get("POSTINOD_ZITADEL_HMAC_SECRET", "").strip()
    if not raw:
        raise ConfigError(
            "POSTINOD_ZITADEL_HMAC_SECRET env var is empty or unset; "
            "set a shared secret matching Zitadel's Action target "
            "(generate via 'openssl rand -hex 32')."
        )
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise ConfigError(
            "POSTINOD_ZITADEL_HMAC_SECRET parsed to zero non-empty values; "
            "rotation overlap takes comma-separated secrets."
        )
    secrets: list[bytes] = []
    for i, part in enumerate(parts):
        # Accept the secret as hex (the documented form, matching the
        # `openssl rand -hex 32` hint). Hex-decode early so the
        # entropy check operates on raw bytes, not hex-char count
        # (A4-A4.6). Non-hex inputs are still accepted as raw bytes
        # for backwards compatibility, but the byte-length floor is
        # enforced uniformly.
        try:
            decoded = bytes.fromhex(part)
            raw = decoded if len(decoded) >= HMAC_MIN_BYTES else part.encode()
        except ValueError:
            raw = part.encode()
        if len(raw) < HMAC_MIN_BYTES:
            raise ConfigError(
                f"POSTINOD_ZITADEL_HMAC_SECRET entry #{i + 1} resolves to "
                f"{len(raw)} raw bytes; minimum is {HMAC_MIN_BYTES} bytes "
                f"(256 bits). Generate via 'openssl rand -hex 32'."
            )
        secrets.append(raw)
    return tuple(secrets)


def read_zitadel_replay_window_sec(env: Mapping[str, str] | None = None) -> int:
    """Read `POSTINOD_ZITADEL_REPLAY_WINDOW_SEC` from env, default 300s.

    Raises `ConfigError` if the env value is set but not a positive int.
    """
    raw = (env or os.environ).get("POSTINOD_ZITADEL_REPLAY_WINDOW_SEC")
    if raw is None or raw.strip() == "":
        return DEFAULT_REPLAY_WINDOW_SEC
    try:
        value = int(raw)
    except ValueError as e:
        raise ConfigError(f"POSTINOD_ZITADEL_REPLAY_WINDOW_SEC={raw!r} is not an integer") from e
    if value <= 0:
        raise ConfigError(f"POSTINOD_ZITADEL_REPLAY_WINDOW_SEC={value} must be a positive int")
    return value
