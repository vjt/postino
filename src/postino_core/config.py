"""Configuration: postino settings + postfix sql-virtual_*.cf parser.

Postfix is the canonical source for SQL credentials. We parse its
existing files instead of duplicating the password in postino's TOML.

Settings load order (pydantic-settings):
  1. Defaults defined here.
  2. /usr/local/etc/postino/postino.toml
  3. ~/.config/postino/postino.toml
  4. POSTINO_* environment variables (highest precedence)."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from postino_core.enums import IdentityBackend, PasswordScheme
from postino_core.errors import ConfigError

_SYSTEM_TOML = Path("/usr/local/etc/postino/postino.toml")
_USER_TOML = Path.home() / ".config" / "postino" / "postino.toml"


class PostfixSqlCredentials(BaseModel):
    """Parsed credentials from sql-virtual_*.cf."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    host: str
    user: str
    password: str
    dbname: str

    def sqlalchemy_url(self) -> str:
        """SQLAlchemy URL for these credentials (PyMySQL driver)."""
        return f"mysql+pymysql://{self.user}:{self.password}@{self.host}/{self.dbname}"


def parse_postfix_sql_cf(path: Path) -> PostfixSqlCredentials:
    """Parse a postfix sql-*.cf file for the connection block.

    Returns: PostfixSqlCredentials with hosts/user/password/dbname.
    Raises: ConfigError if any of the four required fields is missing.
    """
    fields: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        if not _:
            continue
        fields[key.strip()] = value.strip()

    try:
        return PostfixSqlCredentials(
            host=fields["hosts"],
            user=fields["user"],
            password=fields["password"],
            dbname=fields["dbname"],
        )
    except KeyError as e:
        raise ConfigError(f"postfix sql cf missing required field: {e.args[0]}") from e


class PostinoSettings(BaseSettings):
    """Top-level postino configuration."""

    model_config = SettingsConfigDict(
        env_prefix="POSTINO_",
        env_nested_delimiter="__",
        extra="forbid",
        toml_file=[_SYSTEM_TOML, _USER_TOML],
    )

    identity_backend: IdentityBackend
    postfix_sql_dir: Path
    virtual_mailbox_base: Path
    postcreation_hook: Path
    vmail_uid: int
    vmail_gid: int
    default_password_scheme: PasswordScheme
    default_quota_bytes: int

    _toml_paths: ClassVar[tuple[Path, ...]] = (_SYSTEM_TOML, _USER_TOML)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Precedence: env vars > user toml > system toml > init/secret defaults.
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    @model_validator(mode="after")
    def _validate_backend_supported(self) -> PostinoSettings:
        if self.identity_backend is IdentityBackend.ZITADEL:
            raise ConfigError(
                "ZITADEL identity backend is not implemented in this postino build "
                "(MVP supports LOCAL only)"
            )
        return self

    def mailbox_creds(self) -> PostfixSqlCredentials:
        """Resolve mailbox-table credentials from the postfix sql dir."""
        return parse_postfix_sql_cf(self.postfix_sql_dir / "sql-virtual_mailbox_maps.cf")
