# postino

CLI tool to administer a Postfix + Dovecot mail server with the
PostfixAdmin SQL schema as user/alias/domain backend.

Pluggable identity backend (local password column or external IdP via
Zitadel/SCIM). Built for FreeBSD mail hosts but portable.

## Status

Pre-implementation. Design spec: [`docs/superpowers/specs/2026-05-09-postino-design.md`](docs/superpowers/specs/2026-05-09-postino-design.md).

## Install

Not yet released. Once the MVP lands:

```sh
pipx install il-postino
```

Import name remains `postino`. The PyPI distribution is published as
`il-postino` because the bare `postino` name is squatted by an unrelated
2017 package.

## Usage

```sh
# All commands inherit POSTINO_* env vars or read /usr/local/etc/postino/postino.toml

postino domain add example.com --max-mailboxes 100 --default-quota 5G
postino user add foo@example.com --password 'hunter2' --name "Foo Bar" --quota 5G
postino user list --domain example.com --json
postino alias add foo@example.com forwarded@elsewhere.test
postino quota show foo@example.com
postino check
postino status
```

## Configuration

postino reads `/usr/local/etc/postino/postino.toml`. Example:

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

DB credentials are NOT duplicated here — postino parses
`/usr/local/etc/postfix/sql-virtual_mailbox_maps.cf` to extract them.

## Development

Set up a test MySQL/MariaDB schema (see
`docs/superpowers/plans/2026-05-09-postino-mvp.md` § Test Database
Prerequisites for the DDL), then:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
export POSTINO_TEST_DB_URL='mysql+pymysql://postino_test:postino_test_dev@localhost/postino_test'
./scripts/check.sh
```

## License

MIT — see [LICENSE](LICENSE).
