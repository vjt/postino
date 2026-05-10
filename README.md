# postino

![postino — il postino delivers your mail config](https://cdn.jsdelivr.net/gh/vjt/postino@v0.2.0/docs/assets/cover.jpg)

[![PyPI](https://img.shields.io/pypi/v/il-postino.svg)](https://pypi.org/project/il-postino/)
[![Python](https://img.shields.io/pypi/pyversions/il-postino.svg)](https://pypi.org/project/il-postino/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Typed Python CLI for administering Postfix + Dovecot mail servers that use
the [PostfixAdmin](https://github.com/postfixadmin/postfixadmin) SQL schema
as user / alias / domain backend.

Built for FreeBSD mail hosts but portable to Linux. Pluggable identity
backend — local password column today, external IdP (Zitadel / SCIM)
planned for V2.

```sh
pipx install il-postino
postino domain add example.com --max-mailboxes 100 --default-quota 5G
postino user add foo@example.com --quota 5G   # prompts for password
postino check
```

## Why postino

PostfixAdmin's web UI is fine for casual ops, but if you administer mail at
scale you want the operations *scriptable, idempotent, type-safe, and auditable*.
Existing alternatives either reimplement the schema (drift risk), shell out
to mysql (footgun), or wrap PHP (lol no). postino sits directly on top of
the PostfixAdmin schema using SQLAlchemy 2.0 reflection and exposes it as a
proper CLI:

- Pydantic v2 boundary types — every input validated, every row strict-typed
- All ops transactional — `add`, `delete`, status / quota / password updates
- Filesystem rollback on partial failure (maildir mkdir + DB insert atomicity)
- Provider abstraction — swap local-pwd for Zitadel without touching services
- `postino check` — read-only consistency validator (DB ↔ config ↔ filesystem)
- Postfix is the **canonical source for SQL credentials** — postino parses
  `/usr/local/etc/postfix/sql-virtual_*.cf`. No password duplication.

## Install

### Via pipx (workstation, daily admin)

```sh
pipx install il-postino
```

Import name remains `postino`. PyPI distribution is `il-postino` because the
bare `postino` name is squatted by an unrelated 2017 package.

### From git (mail host / production)

For a host where you want a pinned, auditable checkout:

```sh
git clone https://github.com/vjt/postino.git /root/postino
cd /root/postino
python3.13 -m venv .venv
.venv/bin/pip install .

# invoke directly:
/root/postino/.venv/bin/postino check
# or symlink:
ln -s /root/postino/.venv/bin/postino /root/bin/postino
```

To upgrade later:

```sh
cd /root/postino && git pull && .venv/bin/pip install .
```

#### FreeBSD notes

`pydantic-core` is a Rust extension and FreeBSD has no prebuilt wheel.
You need:

```sh
pkg install -y python313 git rust llvm19
export CC=/usr/local/llvm19/bin/clang
export TMPDIR=/root/build-tmp  # if /tmp is noexec
mkdir -p /root/build-tmp
.venv/bin/pip install .
```

`llvm19` is required because the base clang ships incomplete intrinsic
headers (`emmintrin.h` etc. missing) on slimmed-down systems.

The first install caches all compiled wheels into `wheels/`:

```sh
.venv/bin/pip wheel --wheel-dir wheels/ .
```

Future updates can use the cache and skip rust:

```sh
git pull
.venv/bin/pip install --no-build-isolation --find-links wheels/ .
```

## Configuration

postino reads, in order of increasing precedence:

1. `/usr/local/etc/postino/postino.toml`
2. `~/.config/postino/postino.toml`
3. `POSTINO_*` environment variables

Example `postino.toml`:

```toml
identity_backend = "local"
postfix_sql_dir = "/usr/local/etc/postfix"
virtual_mailbox_base = "/srv/mail"
postcreation_hook = "/usr/local/sbin/postfixadmin-mailbox-postcreation.sh"
vmail_uid = 1006
vmail_gid = 1006
default_password_scheme = "BLF-CRYPT"
default_quota_bytes = 1073741824
```

Or via env (CI / containers):

```sh
export POSTINO_IDENTITY_BACKEND=local
export POSTINO_POSTFIX_SQL_DIR=/usr/local/etc/postfix
export POSTINO_VIRTUAL_MAILBOX_BASE=/srv/mail
# ...
```

**DB credentials are NOT in `postino.toml`** — postino parses
`postfix_sql_dir/sql-virtual_mailbox_maps.cf` to extract `host / user /
password / dbname`. Single source of truth.

## Usage

### Domain CRUD

```sh
postino domain add example.com \
    --description "Example domain" \
    --max-mailboxes 100 \
    --max-aliases 200 \
    --default-quota 5G \
    --max-quota 50G \
    --transport virtual

postino domain list
postino domain del example.com --yes
```

### User (mailbox) CRUD

```sh
postino user add foo@example.com \
    --name "Foo Bar" \
    --quota 5G \
    --scheme BLF-CRYPT
# Password is prompted twice (no echo). Never accepted on the command
# line: argv leaks via `ps`, shell history, syslog, and CI logs.

postino user list --domain example.com
postino user list --all                # include disabled
postino user show foo@example.com
postino user passwd foo@example.com    # prompts for new password
postino user enable foo@example.com
postino user disable foo@example.com
postino user quota foo@example.com --set 10G
postino user del foo@example.com --keep-maildir
```

### Aliases

```sh
postino alias add foo@example.com forwarded@elsewhere.test
postino alias list --domain example.com
postino alias del foo@example.com --yes
```

### Quota usage

```sh
postino quota show foo@example.com    # one user
postino quota show                    # all users
```

### Operations

```sh
postino check          # shallow: DB reachable, schema present, hook safe,
                       #          postfix sql-virtual_*.cf credentials match engine.
postino check --deep   # also reconcile mailbox rows ↔ maildirs on disk,
                       # quota2 pairing, alias/mailbox domain FK substitutes,
                       # maildir ownership and Maildir++ skeleton.
postino status         # row counts (domains / mailboxes / aliases / quota2)
```

`postino check` exits 0 when every finding is severity `info`, 4 (`ConfigError`)
when at least one finding is severity `error`. JSON output (`--json`) returns the
full `{findings:[…], ok:bool}` payload for scripting.

### Output formats

All read commands accept `--json` for scripting:

```sh
postino user list --domain example.com --json | jq '.[] | .username'
postino check --json
```

## Exit codes

| Code | Cause                                            |
|------|--------------------------------------------------|
| 0    | success                                          |
| 1    | `NotFoundError` — entity does not exist          |
| 2    | `AlreadyExistsError` — uniqueness conflict       |
| 3    | `CapacityError` — `max_mailboxes` / `max_aliases` exceeded |
| 4    | `ConfigError` — bad / missing config             |
| 5    | `DBError` — DB connectivity / schema drift       |
| 6    | `FilesystemError` — maildir mkdir / chown / rm   |
| 7    | `HookError` — postcreation script returned non-zero |
| 99   | unexpected — bug; full traceback                 |

## Architecture

Two-package wheel, hard separation between library (`postino_core`) and CLI
(`postino`):

```
src/postino_core/    # library, no Typer dep
    enums, errors, quota, password, models, config, db
    fs, hooks, output
    providers/{base,local}
    services/{mailbox,alias,domain,quota,bundle}
    check/consistency

src/postino/         # CLI, depends on postino_core
    cli, commands/{user,alias,domain,quota,check,status,reconcile}
```

Constructor injection throughout. SQL Engine, identity provider, filesystem
adapter, hook runner and clock are all injected — every service is unit
testable in isolation, every integration test starts from a clean
TRUNCATE'd DB. See [`docs/superpowers/specs/2026-05-09-postino-design.md`](docs/superpowers/specs/2026-05-09-postino-design.md)
for the full design.

## Development

```sh
git clone https://github.com/vjt/postino.git
cd postino
python3.13 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

### Test database

Integration + CLI tests need a real MySQL / MariaDB schema where the runner
has full privileges:

```sql
CREATE SCHEMA postino_test
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
CREATE USER 'postino_test'@'localhost' IDENTIFIED BY 'postino_test_dev';
GRANT ALL ON postino_test.* TO 'postino_test'@'localhost';
FLUSH PRIVILEGES;
```

```sh
export POSTINO_TEST_DB_URL='mysql+pymysql://postino_test:postino_test_dev@localhost/postino_test'
```

Unit tests do not need this and always run.

The schema fixture (`tests/fixtures/postfixadmin.sql`) is a
`mysqldump --no-data` of a real PostfixAdmin DB — kept minimal so tests
exercise the actual production schema, not a hand-maintained copy.

### Run the suite

```sh
./scripts/check.sh   # ruff + ruff format --check + pyright + pytest
```

The check script must stay green on every commit. Pyright is in `strict`
mode, ruff has `E F W I B UP RUF SIM` selected.

### Releasing

```sh
# bump version in pyproject.toml
git tag vX.Y.Z
git push origin vX.Y.Z
rm -rf dist/ && python -m build
twine check dist/* && twine upload dist/*
```

Token in `~/.pypirc` under `[pypi]` with `username = __token__`.

## Status

MVP shipping (v0.1.0 on PyPI). Local identity backend implemented.

Next:
- V2: ZitadelProvider — write identity to Zitadel, leave `mailbox.password`
  as `{NOAUTH}` sentinel
- `postino reconcile` — drift detector vs identity source of truth
- TOML config schema validation at startup with helpful errors

## License

MIT — see [LICENSE](LICENSE).
