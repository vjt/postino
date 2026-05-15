"""postino schema — idempotent DB schema migrations.

Applies postino-managed DDL that cannot ship inside PostfixAdmin's own
schema (e.g. the `routes` table introduced in v0.10).  Each migration is
guard-wrapped with ``IF NOT EXISTS`` so running the command twice is safe.
"""

from __future__ import annotations

import os

import typer
from pydantic import ValidationError
from rich.console import Console
from sqlalchemy import URL, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from postino.exit import exit_with_error
from postino_core.config import PostinoSettings
from postino_core.errors import ConfigError, DBError, MailctlError

app = typer.Typer(no_args_is_help=True)

# ---------------------------------------------------------------------------
# DDL constants
# ---------------------------------------------------------------------------

_V010_ROUTES_DDL = """\
CREATE TABLE IF NOT EXISTS `routes` (
  `pattern`      VARCHAR(255) NOT NULL,
  `transport`    VARCHAR(64)  NOT NULL,
  `domain`       VARCHAR(255) NOT NULL,
  `list_address` VARCHAR(255) DEFAULT NULL,
  `priority`     SMALLINT(6)  NOT NULL DEFAULT 50,
  `active`       TINYINT(1)   NOT NULL DEFAULT 1,
  `created`      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`pattern`),
  KEY `idx_domain` (`domain`),
  KEY `idx_list_address` (`list_address`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='postino v0.10+ routing patterns'
"""

_ROUTES_EXISTS_SQL = """\
SELECT COUNT(*) FROM information_schema.tables
WHERE table_schema = DATABASE()
  AND table_name = 'routes'
"""

_VERSION_TABLE_DDL = """\
CREATE TABLE IF NOT EXISTS `postino_schema_version` (
  `id`         TINYINT      NOT NULL DEFAULT 1,
  `version`    VARCHAR(32)  NOT NULL,
  `applied_at` TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                            ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  CONSTRAINT `single_row` CHECK (`id` = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='postino schema version (single row)'
"""

_CURRENT_SCHEMA_VERSION = "v0.12.0"

_VERSION_UPSERT_SQL = """\
INSERT INTO `postino_schema_version` (`id`, `version`) VALUES (1, :version)
ON DUPLICATE KEY UPDATE version = :version, applied_at = CURRENT_TIMESTAMP
"""


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command(
    "migrate",
    help=(
        "Apply all pending postino-managed schema migrations idempotently.\n\n"
        "v0.10: creates the [bold]routes[/bold] table if absent.  "
        "Running twice is safe (CREATE IF NOT EXISTS)."
    ),
    epilog="Run `postino --help` for global options (--json, --quiet, --no-color).",
)
def migrate(
    ctx: typer.Context,
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colors."),
) -> None:
    """Apply the v0.10 routes DDL (and any future migrations) idempotently."""
    # `schema migrate` cannot go through the normal CLI bootstrap
    # (build_services → reflect_schema) because reflect_schema requires
    # routes to already exist — chicken-and-egg.  We load settings and build
    # a raw engine here without reflection.
    no_color_effective = no_color or _env_no_color()
    console = Console(
        color_system=None if no_color_effective else "auto",
        no_color=no_color_effective,
    )

    settings = _load_settings_for_migrate()

    creds = settings.mailbox_creds()
    url = URL.create(
        drivername="mysql+pymysql",
        username=creds.user,
        password=creds.password.get_secret_value(),
        host=creds.host,
        database=creds.dbname,
    )
    engine = create_engine(url, echo=False, future=True)

    try:
        with engine.connect() as probe:
            row = probe.execute(text(_ROUTES_EXISTS_SQL)).scalar()
            existed = bool(row)

        with engine.begin() as conn:
            conn.execute(text(_V010_ROUTES_DDL))

        with engine.begin() as conn:
            conn.execute(text(_VERSION_TABLE_DDL))
            conn.execute(
                text(_VERSION_UPSERT_SQL),
                {"version": _CURRENT_SCHEMA_VERSION},
            )
    except SQLAlchemyError as e:
        console.print(f"[red]✗ migrate failed:[/red] {e}")
        exit_with_error(DBError(f"schema migration failed: {e}"))
    finally:
        engine.dispose()

    if existed:
        console.print("[green]✓[/green] routes table already present — nothing to do.")
    else:
        console.print("[green]✓[/green] routes table created (v0.10).")
    console.print(f"[green]✓[/green] postino_schema_version recorded ({_CURRENT_SCHEMA_VERSION}).")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _env_no_color() -> bool:
    return bool(os.environ.get("NO_COLOR")) or os.environ.get("CI", "").lower() == "true"


def _load_settings_for_migrate() -> PostinoSettings:
    """Load PostinoSettings without going through reflect_schema.

    Mirrors the logic in ``postino.cli._load_settings`` but is defined here
    so schema.py does not import private names from cli.py.  Any config error
    is surfaced via exit_with_error (code 4) before the engine is even built.
    """
    from postino_core.config import config_toml_paths
    from postino_core.config_errors import format_validation_error, load_toml_with_origin

    try:
        return PostinoSettings()  # type: ignore[call-arg]  # WHY: pydantic-settings raises ValidationError for missing fields; same pattern as cli._load_settings.
    except ValidationError as e:
        missing = [err for err in e.errors() if err["type"] == "missing"]
        if missing and not any(p.is_file() for p in config_toml_paths()):
            exit_with_error(
                ConfigError(
                    "config not found: set POSTINO_IDENTITY_BACKEND=local "
                    "(or another POSTINO_* env var)\n"
                    "  or write /usr/local/etc/postino/postino.toml or "
                    "~/.config/postino/postino.toml.\n"
                    f"  missing fields: {', '.join(str(err['loc'][0]) for err in missing)}"
                )
            )
        sources = load_toml_with_origin(list(config_toml_paths()))
        exit_with_error(ConfigError(format_validation_error(e, sources)))
    except MailctlError as e:
        exit_with_error(e)
