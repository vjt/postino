# postino + mlmmj

postino v0.3 ships a `list` command surface that manages
[mlmmj](https://mlmmj.org/) mailing lists alongside PostfixAdmin
mailbox/alias CRUD.

## Topology

- One dedicated subdomain per environment (`lists.<domain>`) whose PA
  `domain` row carries `transport='mlmmj'`. All `<list>@lists.<domain>`
  addresses route through the postfix pipe transport to mlmmj.
- mlmmj's per-list spool dirs live under `mlmmj_spool_dir`
  (default unset; production: `/var/spool/mlmmj`).
- postino owns flag-surface, not on-disk format: every per-list
  operation shells out to mlmmj's bundled binaries
  (`mlmmj-make-ml`, `mlmmj-sub`, `mlmmj-unsub`, `mlmmj-list`).

## Configuration

`/etc/postino/postino.toml` (or env vars):

```toml
mlmmj_spool_dir = "/var/spool/mlmmj"
mlmmj_uid = 117  # uid of the `mlmmj` system user
mlmmj_gid = 124  # gid of the `mlmmj` system user
```

When `mlmmj_spool_dir` is unset, every `postino list` subcommand exits
with code 4 (ConfigError) and a hint to set the env var.

## Postfix wiring (one-time)

Add to `/usr/local/etc/postfix/master.cf` (or the equivalent inside
your `mta` container):

```
mlmmj  unix  -  n  n  -  -  pipe
       flags=DRhu user=mlmmj argv=/usr/bin/mlmmj-receive -L /var/spool/mlmmj/$nexthop
```

postino does not write to `master.cf`. The dedicated `lists.<domain>`
subdomain (with `transport='mlmmj'`) is the only abstraction needed —
mlmmj's listdir lookup resolves per-list addresses.

## Common operations

```sh
# 1. Create the dedicated subdomain (one-time per environment).
postino domain add lists.example.org --transport mlmmj

# 2. Create a list.
postino list add team@lists.example.org \
  --owner alice@example.org \
  --owner bob@example.org

# 3. Add subscribers.
postino list sub team@lists.example.org carol@example.org
postino list sub team@lists.example.org dan@example.org

# 4. Inspect.
postino list show team@lists.example.org
postino list ls --domain lists.example.org

# 5. Remove a subscriber.
postino list unsub team@lists.example.org carol@example.org

# 6. Delete a list (refuses non-empty lists unless --force).
postino list rm team@lists.example.org --yes --force
```

## Out of scope (v0.3)

- Moderation, digest, nomail, archive, bounce handling, custom
  headers/footers, multi-language reply templates.
- SCIM `/Groups` and Zitadel group event mapping → v0.4.
- Reconcile / declarative state.

## Athena deployment

Athena's `docker compose` definition lives outside this repo. Required:

1. `agent` and `mta` services share a `lists_spool:/var/spool/mlmmj`
   docker volume.
2. `mta` Dockerfile installs `mlmmj 1.3.x` (apt on Debian 12 base).
3. `agent` env: `POSTINO_MLMMJ_SPOOL_DIR=/var/spool/mlmmj`, plus
   matching `POSTINO_MLMMJ_UID/GID` for the `mlmmj` system user.
4. `master.cf` carries the mlmmj transport entry (above).

See `tests/postinod_e2e/lists/` for a working compose stub.
