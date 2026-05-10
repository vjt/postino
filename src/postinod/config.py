"""PostinodSettings — daemon-only configuration.

Reads the [postinod] table from the same TOML file PostinoSettings reads
(/usr/local/etc/postino/postino.toml). Daemon-only keys cluster here so
operators see one config file, not two.

Env override prefix: POSTINOD_ (single underscore, no nesting).

The HMAC secret is the only secret postinod stores; it MUST come from
POSTINOD_ZITADEL_HMAC_SECRET env, never from TOML. Startup fails fast
if the env is unset or empty so a misconfigured deployment never boots.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


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
        with self._toml_path.open("rb") as f:
            raw = tomllib.load(f)  # dict[str, Any] from tomllib stubs
        section = raw.get("postinod", {})
        if not isinstance(section, dict):
            return {}
        return section  # type: ignore[return-value]  # WHY: tomllib stubs type values as Any; isinstance guard confirms dict shape but pyright can't narrow the value type further


class PostinodSettings(BaseSettings):
    """Daemon configuration; constructed via `load_postinod_settings`."""

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
    zitadel_hmac_secret: SecretStr = Field(
        ...,
        alias="POSTINOD_ZITADEL_HMAC_SECRET",
        description="Provided ONLY via POSTINOD_ZITADEL_HMAC_SECRET env. "
        "TOML defaults are not allowed for this field.",
    )

    # SCIM surface
    scim_issuer: str
    scim_audience: str
    scim_jwks_refresh_seconds: int = 3600

    @model_validator(mode="after")
    def _validate_hmac_nonempty(self) -> PostinodSettings:
        if not self.zitadel_hmac_secret.get_secret_value():
            raise ValueError(
                "POSTINOD_ZITADEL_HMAC_SECRET env var is empty; "
                "set a non-empty shared secret matching Zitadel's Action target."
            )
        return self


def load_postinod_settings(toml_path: Path) -> PostinodSettings:
    """Build PostinodSettings from `[postinod]` in toml_path + env overrides."""

    class _Configured(PostinodSettings):
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

    return _Configured()  # type: ignore[call-arg]  # WHY: pydantic-settings populates from sources, not init kwargs
