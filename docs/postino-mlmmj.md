# postino + mlmmj

postino v0.10 manages [mlmmj](https://mlmmj.org/) mailing lists alongside
PostfixAdmin mailbox/alias CRUD. The v0.10 architecture replaces the single
dedicated-subdomain transport model with a SQL-driven `routes` table:
postino writes 5 regex routing patterns per list directly into the DB, and
Postfix resolves them via a mysql lookup before falling back to the regular
`domain.transport` column. No `domain.transport='mlmmj'` is involved or
needed.

Spool directories follow a two-level layout: `<spool>/<domain>/<localpart>/`.
A list `team@lists.example.org` lives at
`/var/spool/mlmmj/lists.example.org/team/`. This matches the `$domain/$user`
macro in the `master.cf` pipe service entries and makes per-domain management
straightforward.

## Topology

postino supports two addressing topologies, and you can mix them:

**Dedicated subdomain** — a subdomain whose sole purpose is list addresses
(e.g. `lists.example.org`). All mailboxes on that domain are lists.

```sh
postino domain add lists.example.org --transport virtual
postino list add team@lists.example.org --owner alice@example.org
```

**Shared domain** — lists live on the same domain as regular mailboxes
(e.g. `soci@example.org` alongside `alice@example.org`). The routes table
rows take priority over `virtual_mailbox_maps`; the non-list addresses
resolve normally.

```sh
postino list add soci@example.org --owner board@example.org
```

Both topologies work because routing is per-address via the `routes` table,
not per-domain via `domain.transport`.

## Configuration

`/etc/postino/postino.toml` (or matching env vars):

```toml
mlmmj_spool_dir = "/var/spool/mlmmj"
mlmmj_uid = 117  # uid of the `mlmmj` system user
mlmmj_gid = 124  # gid of the `mlmmj` system user
```

When `mlmmj_spool_dir` is unset, every `postino list` subcommand exits with
code 4 (`ConfigError`) and a hint to set the value.

Postfix MUST have `recipient_delimiter = +-` in `main.cf`; postino validates
this via `postino check`.

## Postfix wiring (one-time)

### main.cf

```
transport_maps =
    mysql:/etc/postfix/sql-routes.cf,
    mysql:/etc/postfix/sql-virtual_transport_maps.cf
recipient_delimiter = +-
virtual_alias_maps = mysql:/etc/postfix/sql-virtual_alias_maps.cf
virtual_mailbox_maps = mysql:/etc/postfix/sql-virtual_mailbox_maps.cf
```

`sql-routes.cf` is first; it handles all list addresses. The existing
`sql-virtual_transport_maps.cf` remains as fallback for non-list domains.

### master.cf

Add these five pipe service blocks. They use `$domain/$user` so the spool
path resolves correctly for both dedicated-subdomain and shared-domain lists.

```
mlmmj-receive unix - n n - - pipe
   flags=DRhu user=mlmmj argv=/usr/bin/mlmmj-receive -L /var/spool/mlmmj/$domain/$user -e ${extension}
mlmmj-bounce unix - n n - - pipe
   flags=DRhu user=mlmmj argv=/usr/bin/mlmmj-bounce -L /var/spool/mlmmj/$domain/$user -a ${sender}
mlmmj-sub unix - n n - - pipe
   flags=DRhu user=mlmmj argv=/usr/bin/mlmmj-sub -L /var/spool/mlmmj/$domain/$user -m ${extension}
mlmmj-unsub unix - n n - - pipe
   flags=DRhu user=mlmmj argv=/usr/bin/mlmmj-unsub -L /var/spool/mlmmj/$domain/$user -m ${extension}
mlmmj-help unix - n n - - pipe
   flags=DRhu user=mlmmj argv=/usr/bin/mlmmj-help -L /var/spool/mlmmj/$domain/$user
```

postino does not write to `master.cf`. These blocks must be present before
`postino check` will report clean.

### sql-routes.cf

```
hosts = 127.0.0.1
user = postfix
password = CHANGEME
dbname = postfix
query = SELECT transport FROM routes WHERE '%s' REGEXP pattern AND domain = SUBSTRING_INDEX('%s', '@', -1) AND active = 1 ORDER BY priority LIMIT 1
```

Replace `hosts`, `user`, `password`, and `dbname` to match your environment.
The query uses the same `%s` substitution as every other Postfix mysql lookup;
Postfix passes the full `local@domain` address as the single interpolation.

### sql-virtual_transport_maps.cf

```
hosts = 127.0.0.1
user = postfix
password = CHANGEME
dbname = postfix
query = SELECT transport FROM domain WHERE domain='%s' AND active = 1
```

This is unchanged from pre-v0.10. It handles all non-list transports
(`virtual`, `lmtp`, `relay`) after `sql-routes.cf` returns no result.

## Required tables

Apply once before starting postinod or running any `postino list` command:

```sh
postino schema migrate
```

The command creates the `routes` table idempotently (safe to run twice).
The DDL is bundled with postino — no manual SQL required.

postino startup reflects the schema and will exit with `ConfigError` if the
`routes` table is absent.

## Common operations

```sh
# Create a list (dedicated subdomain).
postino list add team@lists.example.org \
  --owner alice@example.org \
  --owner bob@example.org

# Create a list (shared domain — lives alongside regular mailboxes).
postino list add soci@example.org \
  --owner board@example.org

# Add a subscriber.
postino list sub team@lists.example.org carol@example.org

# Remove a subscriber.
postino list unsub team@lists.example.org carol@example.org

# Inspect one list (subscribers, owners, route count).
postino list show team@lists.example.org

# List all lists, optionally filtered by domain.
postino list ls
postino list ls --domain lists.example.org

# Delete a list (refuses non-empty unless --force).
postino list rm team@lists.example.org --yes --force
```

`postino list add` writes 5 rows to the `routes` table (one per hyphen-suffix
transport: receive, bounce, sub, unsub, help) plus one `-owner@` alias row in
the `alias` table. `postino list rm` removes all of them.

## Migration from v0.x

Two steps are required when upgrading from v0.3–v0.9:

**1. Apply the DDL** (see [Required tables](#required-tables) above).

**2. Rename existing spool directories** from the flat `<list@domain>` layout
to the two-level `<domain>/<localpart>` layout:

```sh
for d in /var/spool/mlmmj/*@*; do
  [ -d "$d" ] || continue
  name=$(basename "$d")
  lp="${name%@*}"
  dom="${name#*@}"
  mkdir -p "/var/spool/mlmmj/$dom"
  mv "$d" "/var/spool/mlmmj/$dom/$lp"
done
```

See the full migration notes in `CHANGELOG.md` under `[0.10.0]`.

## Validation

`postino check` validates the mlmmj wiring as part of its shallow pass:

- `transport_maps` in `main.cf` lists `sql-routes.cf` before
  `sql-virtual_transport_maps.cf`.
- `recipient_delimiter = +-` is present in `main.cf`.
- All five pipe service blocks (`mlmmj-receive`, `mlmmj-bounce`, `mlmmj-sub`,
  `mlmmj-unsub`, `mlmmj-help`) are present in `master.cf`.
- Every active list address has a corresponding `-owner@<domain>` alias row.

Run `postino check --json` for a machine-readable findings payload.
