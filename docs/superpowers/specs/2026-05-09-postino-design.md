# postino — Design Spec

**Date**: 2026-05-09
**Author**: vjt
**Status**: Draft, pre-implementation

## 1. Purpose

`postino` is a Python CLI to administer a Postfix + Dovecot mail server
backed by the **PostfixAdmin** SQL schema (MySQL/MariaDB). It replaces
hand-edited SQL and ad-hoc shell with a typed, validated, auditable
interface to user, alias, domain and quota lifecycle.

Designed first for `m42.openssl.it` (FreeBSD 14, MariaDB 8.4, Postfix
3.x, Dovecot 2.4) but portable to any PostfixAdmin-schema deployment.

The tool MUST be pluggable on identity: it ships with a `local`
provider that reads/writes passwords directly in the
`mailbox.password` column (current m42 behavior), and reserves a clean
seam for a future `zitadel` provider that delegates identity to an
external OIDC IdP.

## 2. Engineering Standards

This project follows **vjt's Python engineering standards** as
documented in `~/code/ha-verisure-italy/CLAUDE.md`. Summary of the
constraints that shape this spec:

- **Pydantic models only** — no `@dataclass`, no naked dicts crossing
  boundaries.
- **Type annotations on every signature**; pyright strict.
- **Enums over dicts** for constant mappings.
- **No default arguments** unless a genuine config value
  (`timeout=30`); `=None` defaults are forbidden.
- **No `.get()` with fallbacks** on data that must exist.
- **Parse at the boundary, crash inside.** Pydantic at I/O; types
  guarantee correctness in the core.
- **State the contract**: signature + failure mode in one sentence
  before implementing.
- **Constructor injection.** No globals, no singletons.
- **Return domain types, not strings or dicts.**
- **One feature, one code path** — no copy-paste with tweaks.
- **Crash loud on unexpected input.**
- **Fix root causes** — no `# type: ignore` band-aids.
- **Bite-sized commits** — one logical change, WHY in the message.

The same standards apply to tests:

- Assert outcomes, not call sequences.
- Mock at boundaries (DB, FS, subprocess, clock); real deps inside.
- Use production code in tests; never hardcode expected strings.
- Never weaken production code to make tests pass.

Tooling:

- **pyright** strict, with `include` limited to project sources.
- **ruff** linter + formatter.
- **pytest** (`pytest tests/ -x -q`).
- **`./scripts/check.sh`** chains all three; CI gate.

## 3. Architecture

```
postino/
  src/postino_core/                 # library
    __init__.py
    config.py                       # pydantic-settings → loads TOML + parses
                                    #   /usr/local/etc/postfix/sql-virtual_*.cf
                                    #   secrets stay in postfix files
    db.py                           # SQLAlchemy Engine factory, schema reflection
    models.py                       # Pydantic: Mailbox, Alias, Domain, Quota,
                                    #   MailboxCreate, MailboxUsage, …
    enums.py                        # MailboxStatus, PasswordScheme, QuotaUnit,
                                    #   DomainTransport, AuthBackend
    errors.py                       # MailctlError hierarchy
    output.py                       # Rich tables; --json via .model_dump_json()
    services/
      mailbox.py                    # MailboxService — provider-agnostic metadata
      alias.py                      # AliasService
      domain.py                     # DomainService
      quota.py                      # QuotaService
    providers/
      base.py                       # IdentityProvider Protocol
      local.py                      # LocalProvider — MVP
      # zitadel.py                  # V2 only, not in MVP
    check/
      consistency.py                # postino check — config validation
                                    #   (no template generation in MVP)

  src/postino/                      # Typer CLI, imports postino_core
    __init__.py
    cli.py                          # entrypoint; mounts subcommand groups
    commands/
      user.py                       # user add/del/list/show/passwd/enable/…
      alias.py                      # alias add/del/list
      domain.py                     # domain add/del/list
      quota.py                      # quota set/show
      check.py                      # check
      reconcile.py                  # reconcile (V2-friendly stub)

  # V2 add-on (out of MVP scope, sketched here only):
  # src/postino_d/                  # FastAPI SCIM 2.0 server
  #   scim.py                       # /scim/v2/Users {GET,POST,PATCH,DELETE}

  tests/
    conftest.py                     # ephemeral DB schema fixture, tmp maildir, frozen clock
    unit/                           # pure logic
    integration/                    # real DB + real FS
    cli/                            # Typer CliRunner end-to-end

  scripts/
    check.sh                        # ruff + pyright + pytest

  pyproject.toml                    # name = "il-postino"; packages = postino, postino_core
  README.md
  LICENSE                           # MIT
  docs/
    superpowers/specs/              # design + plan docs
```

**Layering**:

- `commands/*` (CLI) → `services/*` (domain logic) → `db.py` (boundary).
- `services/*` consume `IdentityProvider` for password / identity ops;
  the provider is constructor-injected, no global access.
- Pydantic models are the only types crossing `services` ↔ `commands`.
  Raw SQLAlchemy `Row` objects are confined inside `services` and
  `db.py`.
- `output.py` is the only module that knows about Rich. Models flow
  through it; commands never call `console.print` directly.

## 4. Data Model

### 4.1 Enums (`enums.py`)

```python
from enum import IntEnum, StrEnum


class MailboxStatus(IntEnum):
    """Maps to PA mailbox.active column."""
    ACTIVE = 1
    DISABLED = 0


class PasswordScheme(StrEnum):
    """dovecot pass_scheme tags. Existing m42 rows are MD5-CRYPT (legacy
    PostfixAdmin default). New mailboxes default to BCRYPT (BLF-CRYPT).
    Dovecot reads {scheme} from the row prefix; default_pass_scheme
    covers legacy unprefixed rows."""
    MD5_CRYPT = "MD5-CRYPT"
    BCRYPT = "BLF-CRYPT"
    SHA512_CRYPT = "SHA512-CRYPT"


class QuotaUnit(StrEnum):
    """Suffix → multiplier (binary). 5G == 5 * 1024**3 bytes."""
    B = "B"
    K = "K"
    M = "M"
    G = "G"
    T = "T"


class DomainTransport(StrEnum):
    VIRTUAL = "virtual"
    LMTP = "lmtp:unix:private/dovecot-lmtp"
    RELAY = "relay"


class IdentityBackend(StrEnum):
    """Selects which IdentityProvider postino uses at runtime."""
    LOCAL = "local"
    ZITADEL = "zitadel"   # V2 only; CLI errors out if requested in MVP build
```

### 4.2 Models (`models.py`)

All models are `frozen=True, strict=True` Pydantic v2 `BaseModel`s.
Strict mode disables type coercion (e.g. `1` → `"1"` is rejected).

```python
class Mailbox(BaseModel):
    """A parsed mailbox row.

    Returns: a validated mailbox.
    Raises: pydantic.ValidationError on schema mismatch.
    """
    model_config = ConfigDict(frozen=True, strict=True)
    username: EmailStr
    name: str                # display name; empty string allowed but explicit
    maildir: Path            # relative to virtual_mailbox_base
    quota_bytes: int         # 0 = unlimited (PA convention)
    local_part: str
    domain: str
    status: MailboxStatus
    created: datetime
    modified: datetime


class MailboxCreate(BaseModel):
    """Inputs for `postino user add`. Built at the CLI boundary."""
    model_config = ConfigDict(frozen=True, strict=True)
    username: EmailStr
    password: SecretStr
    name: str                # caller supplies "" if absent
    quota_bytes: int
    scheme: PasswordScheme   # caller decides; CLI default = BCRYPT


class MailboxUsage(BaseModel):
    """Live usage from quota2 table."""
    model_config = ConfigDict(frozen=True, strict=True)
    username: EmailStr
    bytes_used: int
    messages: int


class Alias(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    address: EmailStr
    goto: str                # may be a comma-list of dest addresses
    domain: str
    status: MailboxStatus
    created: datetime
    modified: datetime


class Domain(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    domain: str
    description: str
    max_aliases: int         # 0 = unlimited
    max_mailboxes: int
    max_quota_bytes: int     # per-mailbox cap
    default_quota_bytes: int
    transport: DomainTransport
    backupmx: bool
    status: MailboxStatus
    created: datetime
    modified: datetime
```

### 4.3 Notes

- `EmailStr` validates syntax at the boundary; IDN domains supported.
- `quota_bytes: int` everywhere. Parsing of `"5G"` happens at the CLI
  layer; `int` is the canonical form internally.
- `SecretStr` for passwords prevents accidental leakage in `repr` and
  log lines.
- Naming is `created`/`modified` to mirror PostfixAdmin's column names.
- No `Optional` fields except where a column is genuinely nullable
  (e.g. some PA columns can be `NULL` — those become `T | None`).
- The PA `mailbox` table carries additional columns not modeled in
  MVP: `phone`, `email_other`, `token`, `token_validity`,
  `password_expiry`. These support PostfixAdmin's web-UI password
  reset flow. postino leaves them at column defaults on INSERT and
  ignores them on read. They become first-class when the self-service
  web UI lands (see §13).

## 5. Identity Provider Protocol

`IdentityProvider` is the single seam between the local PA-based mode
and a future Zitadel-delegated mode. The Protocol is defined in MVP;
only `LocalProvider` is implemented. `ZitadelProvider` lands in V2.

The PA `mailbox.password` column is `VARCHAR(255) NOT NULL`. Zitadel
mode therefore stores a sentinel value (`{NOAUTH}` — not a valid
dovecot scheme tag, so dovecot's SQL passdb will return "user found,
no password match" and chain through to the LDAP passdb that binds to
Zitadel).

```python
# providers/base.py
from typing import Protocol
from sqlalchemy import Connection
from pydantic import EmailStr, SecretStr


class IdentityProvider(Protocol):
    """Owner of authentication identity for a mailbox.

    LocalProvider stores credentials in PA mailbox.password (with
    {scheme} prefix). ZitadelProvider creates the identity in Zitadel
    and writes the {NOAUTH} sentinel to mailbox.password; dovecot is
    configured with a chained SQL → LDAP passdb so authentication
    flows through to Zitadel.
    """

    def create_identity(
        self,
        conn: Connection,
        username: EmailStr,
        name: str,
        password: SecretStr,
        scheme: PasswordScheme,
    ) -> None:
        """Establish the identity in the auth source.

        Called by MailboxService.add immediately after the mailbox row
        has been INSERTed (with a sentinel password). For LocalProvider
        this UPDATEs the row's password column with the hashed value.
        For ZitadelProvider this calls Zitadel's CreateUser API; the
        password column is left at the {NOAUTH} sentinel.

        Returns: None on success.
        Raises: ConfigError if the scheme is unsupported by the
                provider; DBError or provider-specific subclass on
                external API failure.
        """

    def set_password(
        self,
        conn: Connection,
        username: EmailStr,
        password: SecretStr,
        scheme: PasswordScheme,
    ) -> None:
        """Change the password for an existing identity.

        Returns: None on success.
        Raises: NotFoundError if the identity does not exist;
                ConfigError if the scheme is unsupported.
        """

    def delete_identity(
        self,
        conn: Connection,
        username: EmailStr,
    ) -> None:
        """Remove the identity from the auth source.

        Idempotent: no error if the identity does not exist.

        For LocalProvider this is a no-op — the mailbox row deletion
        carried out by MailboxService.delete already removes the
        password column. For ZitadelProvider this calls
        DeleteUser against the Zitadel Management API.

        Returns: None.
        Raises: ConfigError on provider-specific failure.
        """

    def supports_password_change(self) -> bool:
        """Whether `set_password` is exposed via the CLI.

        LocalProvider returns True. ZitadelProvider returns False;
        `postino user passwd` is hidden from --help and errors with
        ConfigError if invoked.
        """
```

The `conn: Connection` parameter is the SQLAlchemy connection of the
enclosing transaction. LocalProvider participates in that transaction
(its UPDATE/UPDATE/DELETE go through `conn`). ZitadelProvider ignores
`conn` for API calls but receives it to keep the Protocol shape
identical — and to make compensation (saga rollback) easier when
ZitadelProvider needs to detect that the outer transaction has rolled
back.

`ZitadelProvider` (V2) saga rules will be specified when V2 lands.
Sketch: a Zitadel `create_identity` call that succeeds followed by a
post-DB-commit FS or hook failure must call `delete_identity` to undo
in Zitadel before propagating the error.

## 6. Data Flow

Example: `postino user add foo@example.com --quota 5G --name "Foo"`

```
1. Typer parses flags. "5G" → 5 * 1024**3 = 5368709120.
2. CLI builds MailboxCreate(...) — Pydantic validates EmailStr,
   SecretStr, scheme enum membership.
3. ServicesBundle is constructed from config (DB engine, IdentityProvider,
   clock, hooks). Constructor injection.
4. Inside engine.begin() (single DB tx):
     a. SELECT … FOR UPDATE on the domain row     → NotFoundError if absent.
     b. Check current mailbox count < max         → CapacityError.
     c. Check username uniqueness                  → AlreadyExistsError.
     d. INSERT INTO mailbox with sentinel password ('{NOAUTH}').
     e. INSERT INTO quota2 (bytes=0, messages=0).
     f. provider.create_identity(conn, …) — for LocalProvider this
        UPDATEs the row's password column with the hashed value
        (replacing the sentinel). For ZitadelProvider this calls the
        Zitadel CreateUser API; the password column stays at the
        sentinel value.
     g. INSERT INTO log (audit row).
   COMMIT on context exit.
5. Filesystem + hook (outside DB tx but inside outer try):
     h. mkdir maildir, chown to vmail uid/gid
     i. Run /usr/local/sbin/postfixadmin-mailbox-postcreation.sh
6. On any exception in steps h/i: shutil.rmtree(maildir) and re-raise.
   The DB rolled back in (4) automatically when an exception escaped
   engine.begin().
7. CLI renders the resulting Mailbox via output.render(...) in human
   or JSON form depending on --json.
```

**Atomicity contract**: a failed `add` leaves no observable state — no
DB row, no maildir, no audit row beyond the standard tx rollback. The
implementation MUST be tested with forced failures at h/i to prove
this.

## 7. Error Handling

```python
# errors.py
class MailctlError(Exception):
    """Base for all expected failures. CLI top-level catches this."""

class ConfigError(MailctlError): ...        # bad config / unsupported scheme
class DBError(MailctlError): ...            # connection, schema drift
class NotFoundError(MailctlError): ...      # SELECT returned 0 rows
class AlreadyExistsError(MailctlError): ... # uniqueness violation
class CapacityError(MailctlError): ...      # domain caps hit
class FilesystemError(MailctlError): ...    # mkdir / chown / rm
class HookError(MailctlError): ...          # postcreation script failed
```

**CLI exit codes**:

| Exit | Cause |
|------|-------|
| 0    | Success |
| 1    | NotFoundError |
| 2    | AlreadyExistsError |
| 3    | CapacityError |
| 4    | ConfigError |
| 5    | DBError |
| 6    | FilesystemError |
| 7    | HookError |
| 99   | Any other exception (bug — Rich traceback shown) |

Anything outside `MailctlError` is a bug. The CLI prints `rich.traceback`
and exits 99. No silent fallthrough, no `except Exception: pass`.

## 8. Configuration

`config.py` uses `pydantic-settings`. Sources, in order:

1. `/usr/local/etc/postino/postino.toml` (system)
2. `~/.config/postino/postino.toml` (user)
3. `POSTINO_*` environment variables (override)

DB credentials are not duplicated: `config.py` parses
`/usr/local/etc/postfix/sql-virtual_mailbox_maps.cf` to extract the
`hosts/user/password/dbname` block. Postfix is the canonical source.

Schema:

```toml
[identity]
backend = "local"                 # IdentityBackend.LOCAL or .ZITADEL
                                  # MVP only accepts "local"

[paths]
postfix_sql_dir = "/usr/local/etc/postfix"
virtual_mailbox_base = "/srv/mail"
postcreation_hook = "/usr/local/sbin/postfixadmin-mailbox-postcreation.sh"

[vmail]
uid = 1006
gid = 1006

[defaults]
password_scheme = "BLF-CRYPT"     # PasswordScheme
default_quota_bytes = 1073741824  # 1 GiB

[output]
default_format = "human"          # "human" | "json"
```

Validation: when `[identity].backend = "zitadel"` is requested in an
MVP build, `config.py` raises `ConfigError` immediately.

## 9. CLI Surface

```
postino user add <email> [--password=… | --password-prompt] [--name=…]
                          [--quota=5G] [--scheme=BLF-CRYPT]
postino user del <email> [--keep-maildir] [--yes]
postino user list [--domain=…] [--disabled] [--json]
postino user show <email> [--json]
postino user passwd <email> [--password=… | --password-prompt]
postino user enable <email>
postino user disable <email>
postino user quota <email> [--set=5G | --show]

postino alias add <addr> <goto> [--multiple-goto-comma-allowed]
postino alias del <addr>
postino alias list [--domain=…] [--json]

postino domain add <domain> [--max-mailboxes=N] [--default-quota=…]
                            [--transport=…] [--description=…]
postino domain del <domain> [--yes]
postino domain list [--json]

postino quota show [<email>|--all] [--json]

postino check                       # consistency-check, see §10
postino reconcile                   # placeholder; full meaning in V2

postino status                      # daily-ops snapshot:
                                    #   mailbox count, queue depth,
                                    #   imap connections, rspamd msg/sec,
                                    #   recent errors

postino --version
postino --help
```

Global flags: `--config=<path>`, `--json`, `--quiet`, `--yes` (skip
confirmation prompts on destructive ops).

## 10. `postino check` — Consistency Validator

`check` is the MVP's only direct concession to "config awareness". It
**does not generate** postfix or dovecot configs. It validates that
postino's assumptions match what's installed:

- Postfix `virtual_mailbox_base` matches `[paths].virtual_mailbox_base`.
- Postfix SQL credentials in `sql-virtual_*.cf` match what postino
  resolved.
- Dovecot SQL `password_query` references the same `mailbox` table and
  expected column names.
- vmail uid/gid in `[vmail]` match dovecot config.
- (When `[identity].backend = "zitadel"` lands) dovecot has an LDAP
  passdb and the `passdb { driver = sql }` block is either absent or
  marked `result_failure = continue`.
- Postcreation hook is executable.

Output: green check / red cross per assertion, exit code 0 if all
green, non-zero otherwise. Does not modify any file.

## 11. Testing Strategy

### Layers

```
tests/
  unit/             # pure logic, no I/O, no fixtures beyond Pydantic models
  integration/      # real DB on a dedicated test schema, real /tmp maildir
  cli/              # Typer CliRunner end-to-end against the same test DB
```

### DB fixture

A dedicated `mailctl_test` schema on the same MariaDB server. Schema
loaded once per session from a checked-in `tests/fixtures/postfixadmin.sql`
dump. Each test truncates all tables in `conftest.py` for isolation.

This trades isolation purity for ergonomic simplicity: no Docker, no
testcontainers, no mysqld-spawning. Acceptable because postino is a
single-admin tool with no concurrency concerns at test time.

### What is tested

| Layer       | Subject                                      |
|-------------|----------------------------------------------|
| unit        | quota parser (`"5G"` → bytes, `"5x"` raises) |
| unit        | password hashing roundtrip per scheme        |
| unit        | Pydantic model edge cases                    |
| integration | Service CRUD success paths                   |
| integration | All error paths raise the documented type    |
| integration | Atomicity: forced FS failure → no DB row     |
| integration | `check` against a known-good fixture stack   |
| cli         | Each subcommand smoke + JSON parse roundtrip |

### What is not tested

- Pydantic's own validators (covered upstream).
- Typer's argparse behavior.
- SQLAlchemy connection pooling.

### Frozen clock

A `Clock` Protocol (`__call__() -> datetime`) is constructor-injected
into services. Tests pass a fixed-value clock so `created` and
`modified` are deterministic. Production wires `datetime.now`.

### Outcome assertions

```python
# Wrong:
mock_engine.execute.assert_called_once_with("INSERT INTO mailbox …")

# Right:
created = svc.add(MailboxCreate(...))
assert created == svc.get(created.username)
assert isinstance(created, Mailbox)
```

## 12. Deployment Modes

postino supports **one deployment mode per install**, picked at config
time. The two modes share 100% of the codebase; the only runtime
difference is which `IdentityProvider` is wired.

| Mode    | Identity source       | mail.password column | dovecot passdb | postino CLI surface |
|---------|-----------------------|----------------------|----------------|---------------------|
| local   | PA `mailbox.password` | hashed `{scheme}` value | `driver = sql` | `user passwd` enabled |
| zitadel | Zitadel via OIDC/LDAP | sentinel `{NOAUTH}` (column is NOT NULL) | chained `sql` → `ldap` (LDAP-bind to Zitadel) | `user passwd` hidden |

**Why one mode per install**: per-user mixed mode (some users in PA,
others in Zitadel within the same install) was considered and rejected.
It would require a new `auth_backend` column on `mailbox` and dovecot
passdb chaining, with split-brain risk during migration. The simpler
deployment-mode blend covers the realistic use case (a fresh
greenfield install picks zitadel; m42 stays local) without that
complexity.

## 13. Scope

### MVP (this spec, this implementation pass)

- `postino_core` library with provider Protocol defined.
- `LocalProvider` only.
- CLI subcommands: `user`, `alias`, `domain`, `quota`, `check`,
  `status`, `reconcile` (stub).
- Tests as described in §11.
- pyproject.toml, scripts/check.sh, MIT license.
- PyPI distribution name: `il-postino`. Import name: `postino`.

### V2 (out of scope here, sketched only)

- `ZitadelProvider` — httpx + Zitadel Management API.
- `postino_d` — FastAPI SCIM 2.0 server. Imports same `postino_core.services`.
- `postino reconcile --source zitadel` for drift detection.
- Dovecot LDAP passdb wiring (ops change, documented in `docs/ops/`).

### V3+ (deferred)

- Optional bootstrap mode: `postino setup --emit-configs` to generate
  initial postfix/dovecot config files for new installs.
- Vacation autoresponder management.
- Fetchmail config management.
- Multi-server orchestration (manage multiple m42-like hosts).
- **Schema refactor**: postino owns the schema once it has been the
  exclusive writer for several months. The PostfixAdmin layout has
  warts (MyISAM, parallel `quota`/`quota2` tables, `domain.aliases`
  and `domain.mailboxes` columns mixing caps with semantics, no FK
  constraints, VARCHAR-typed timestamps on legacy MariaDB versions).
  A `postino migrate <version>` command would coordinate schema
  evolution with the matching update to
  `/usr/local/etc/postfix/sql-virtual_*.cf` and dovecot's
  `password_query` / `user_query`. This becomes possible only after
  postino is the canonical gatekeeper — until then, schema changes
  break direct postfix/dovecot SQL access.

### Web UI (separate roadmap track)

A web frontend is out of MVP scope but on the roadmap. Two audiences,
two scopes:

- **Self-service UI** for mail users: change own password, set
  vacation auto-reply, view current quota. High ROI — currently every
  password change is a sysadmin ticket. Likely **V2** alongside
  ZitadelProvider, since the self-service flow shares plumbing with
  OIDC delegation.
- **Admin UI** for vjt: web wrapper of the CLI surface. Low ROI
  (the CLI already covers it); **V3+** at most.

Stack when built: **FastAPI + HTMX + Jinja2**, no SPA. Sessions via
`itsdangerous`-signed cookies. Login uses the active
`IdentityProvider`: `LocalProvider.verify_password` in local mode,
OIDC redirect to Zitadel in delegated mode. The web UI imports the
same `postino_core.services` library — no logic duplication.

## 14. Open Questions

None at spec time. Resolved during brainstorming:

- Language: Python (decided).
- CLI framework: Typer + Rich + Pydantic + SQLAlchemy Core (decided).
- Deployment: local on m42 as root (decided).
- MVP focus: user/mailbox CRUD first, full surface in this spec.
- Identity blend: deployment-mode level, one per install (decided).
- Config gen: validation only (`check`), no generation in MVP.
- Naming: project `postino`, PyPI `il-postino`.
- License: MIT.
- Repo: `github.com/vjt/postino`, public.

## 15. References

- vjt's Python engineering standards: `~/code/ha-verisure-italy/CLAUDE.md`
- PostfixAdmin schema: `https://github.com/postfixadmin/postfixadmin`
- Pydantic v2: `https://docs.pydantic.dev/latest/`
- Typer: `https://typer.tiangolo.com/`
- SQLAlchemy 2.0 Core: `https://docs.sqlalchemy.org/en/20/core/`
- Rich: `https://rich.readthedocs.io/`
- Zitadel SCIM (V2): `https://zitadel.com/docs/apis/scim2`
