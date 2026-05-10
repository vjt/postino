"""Configuration: postino settings + postfix sql-virtual_*.cf parser.

Postfix is the canonical source for SQL credentials. We parse its
existing files instead of duplicating the password in postino's TOML.

Settings load order (pydantic-settings):
  1. Defaults defined here.
  2. /usr/local/etc/postino/postino.toml
  3. ~/.config/postino/postino.toml
  4. POSTINO_* environment variables (highest precedence)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from tomllib import TOMLDecodeError
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator
from pydantic.fields import FieldInfo
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
    """Parsed credentials from sql-virtual_*.cf.

    The password is held as ``SecretStr`` so accidental ``repr`` /
    ``str`` / log paths render ``**********`` instead of the cleartext.
    Call ``sqlalchemy_url`` only when handing the URL to SQLAlchemy.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    host: str
    user: str
    password: SecretStr
    dbname: str

    def sqlalchemy_url(self) -> str:
        """SQLAlchemy URL for these credentials (PyMySQL driver).

        ``get_secret_value`` is called only at the return statement —
        keep the cleartext on the stack for as short as possible.
        """
        return (
            f"mysql+pymysql://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}/{self.dbname}"
        )

    def __repr__(self) -> str:
        # Belt-and-braces over Pydantic's SecretStr redaction: a custom
        # repr defends against future BaseModel subclassing edge cases
        # and makes the redaction explicit at this boundary.
        return (
            f"PostfixSqlCredentials(host={self.host!r}, user={self.user!r}, "
            f"dbname={self.dbname!r}, password=***)"
        )


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
            password=SecretStr(fields["password"]),
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
    postcreation_hook_timeout: float = 30.0
    vmail_uid: int
    vmail_gid: int
    default_password_scheme: PasswordScheme
    default_quota_bytes: int
    # Postfix transport_maps nexthop appended to `lmtp:` for domains with
    # `transport=lmtp`. Default targets dovecot-lmtp's unix socket on the
    # canonical PA + dovecot layout.
    lmtp_destination: str = "unix:private/dovecot-lmtp"

    # mlmmj (v0.3+) — optional. When `mlmmj_spool_dir` is None, the
    # `MailingListService` is still wired but every mutating call raises
    # ConfigError. uid/gid sentinel `-1` mirrors FilesystemAdapter's
    # "do not drop privileges" convention used in tests + dev.
    mlmmj_spool_dir: Path | None = None
    mlmmj_uid: int = -1
    mlmmj_gid: int = -1

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
        # Positive allow-list: any future backend is rejected until it
        # ships in `services/bundle.py::build_services`. Order: easier to
        # add a backend than to forget removing this guard.
        supported = (IdentityBackend.LOCAL, IdentityBackend.NOAUTH)
        if self.identity_backend not in supported:
            raise ConfigError(
                f"identity_backend {self.identity_backend.value!r} not supported "
                f"(supported: {[b.value for b in supported]})"
            )
        return self

    def mailbox_creds(self) -> PostfixSqlCredentials:
        """Resolve mailbox-table credentials from the postfix sql dir."""
        return parse_postfix_sql_cf(self.postfix_sql_dir / "sql-virtual_mailbox_maps.cf")


class _PostinoTomlSource(PydanticBaseSettingsSource):
    """Reads the top-level postino keys from a postino.toml file.

    Subtable sections (e.g. ``[postinod]``) are silently dropped so that
    ``PostinoSettings(extra="forbid")`` does not reject daemon-only keys
    when both postino and postinod settings live in the same file.
    """

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
        # Drop subtable entries (dicts) — they belong to daemon-only sections
        # like [postinod] and must not be passed to PostinoSettings which has
        # extra="forbid". Scalar and list values are postino-level config.
        return {
            k: v
            for k, v in raw.items()  # type: ignore[union-attr]  # WHY: tomllib stubs type load() as dict[str, Any]; isinstance on the outer object is implicit; we iterate the top-level dict safely
            if not isinstance(v, dict)
        }


def load_postino_settings(toml_path: Path) -> PostinoSettings:
    """Build PostinoSettings from a specific TOML file + env overrides.

    Constructs a runtime subclass that overrides the settings source so an
    arbitrary path is read instead of the hardcoded system/user defaults.
    Subtable sections (e.g. ``[postinod]``) in the TOML are silently dropped
    so that ``PostinoSettings(extra="forbid")`` does not reject daemon-only
    keys when both postino and postinod settings live in the same TOML file.
    """

    class _PostinoSettingsImpl(PostinoSettings):
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
                _PostinoTomlSource(settings_cls, toml_path),
                file_secret_settings,
            )

    return _PostinoSettingsImpl()  # type: ignore[call-arg]  # WHY: pydantic-settings populates from sources, not init kwargs
