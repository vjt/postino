# postino — roadmap

## v0.1.1 — small, ready-now

All commits already in `main` post the v0.1.0 tag. Just need to bump
+ retag + republish.

- TOML config loader (`PostinoSettings` reads
  `/usr/local/etc/postino/postino.toml` + `~/.config/postino/postino.toml`)
- Friendly config error (translate Pydantic `ValidationError` →
  `ConfigError` with a human message naming missing fields)
- `--json` placement fix in tests (it's a top-level option, not per-cmd)
- `scripts/check.sh` auto-loads `.env`
- CI workflows (`ci.yml`, `release.yml` with PyPI OIDC trusted publishing)
- `scripts/ci-watch.sh` — poll a CI run until completion
- `tests/conftest.py` drops stale tables before replay
- `.env.example`

To cut:

```sh
. .venv/bin/activate
sed -i 's/^version = "0.1.0"/version = "0.1.1"/' pyproject.toml
./scripts/check.sh
git commit -am 'release: 0.1.1 — TOML config, friendlier errors, CI'
git tag v0.1.1
git push origin main v0.1.1
# CI release.yml publishes via OIDC trusted publishing — set the publisher
# at https://pypi.org/manage/project/il-postino/settings/publishing/
# Manual fallback: rm -rf dist/ && python -m build && twine upload dist/*
```

## v0.2 — operator quality of life

1. **`postino reconcile`** — drift detector. Reads an "expected state"
   declaration (TOML/YAML — domains, mailboxes, aliases, quotas) and
   diffs against live DB. Reports drift; `--apply` to converge.
   Foundation for IaC-style mail admin.
2. **Per-subcommand `--json`** — repeat the flag on every command so
   `postino user list --json` works. Currently top-level only.
3. **TOML schema validation at startup** — better errors than the
   Pydantic ValidationError → ConfigError translation. Show file:line
   of the offending field.
4. **`postino check` extensions**:
   - `vmail` uid/gid resolves to a real user/group on the host
   - postcreation hook is syntactically valid (`sh -n`)
   - DB user has *exactly* the grants postino needs (not over-privileged)
   - `postfix sql-virtual_*.cf` files are not world-readable
5. **`CHANGELOG.md`** + GitHub Releases auto-populated from tags.
6. **`postino rotate-db-pwd`** — rotate the postfix DB user atomically:
   `ALTER USER`, rewrite all referenced `sql-virtual_*.cf` and dovecot
   sql configs, `postfix reload` + `dovecot reload`, write an audit-log
   entry. Encodes the manual workflow as a single command.
7. **`postino dump-schema`** — produce `tests/fixtures/postfixadmin.sql`
   directly with mysqldump warnings / GTID statements / non-DDL noise
   stripped, so the fixture doesn't need hand-editing.
8. **Shell completion** — `postino --install-completion`.
9. **Audit log** — postino writes its own ops to the PostfixAdmin `log`
   table so PA web UI shows them.

## v0.3 — mlmmj mailing lists (shipped 2026-05-10)

`postino list add/sub/unsub/show/ls/rm` shells out to mlmmj 1.3.x
binaries against a `lists.<domain>` PA subdomain with `transport='mlmmj'`.
Operator notes: `docs/postino-mlmmj.md`. Design spec:
`docs/superpowers/specs/2026-05-10-postino-v0.3-mlmmj-design.md`.

## v0.4 — hardening cluster 1 (shipped 2026-05-11)

Six-task hardening pass:
1. HMAC + JWKS hardening — env-only secret, entropy floor, rotation
   overlap, replay window, unknown-kid cooldown, stale-serve max age.
2. Audit transaction contract — `AuditWriter` Protocol, atomic
   postino + postinod dual-row writer, injected actor callable.
3. `domain.delete --force` privacy fix — FS-before-DB ordering,
   `keep_maildir` flag, orphan check in `check --deep`.
4. SCIM `meta` block (RFC 7643 §3.1), `/Schemas` derived from
   pydantic introspection, scim2-models e2e validation.
5. NoAuth provider safety — `build_app` startup ConfigError when
   `identity_backend != noauth`, dovecot passdb chain probe in
   `check --deep`, real conformance tests across Local + NoAuth.

Breaking: `POSTINOD_ZITADEL_HMAC_SECRET` is env-only (no TOML);
minimum 32 bytes. Spec: `docs/superpowers/specs/2026-05-10-postino-v0.4-hardening.md`.

## v0.5 — mlmmj e2e + post-review hardening (shipped 2026-05-11)

Filesystem & mlmmj adapters hardened against symlink/race attacks:
component-wise lstat walk, `Path.is_relative_to` containment,
`os.chown(follow_symlinks=False)`, `os.setgroups([])` before setgid
in mlmmj preexec, `MlmmjError` on missing binaries, no
`ignore_errors=True` in rollback. CI 0-skip enforcement. Spec:
`docs/superpowers/specs/2026-05-11-postino-v0.5-mlmmj-e2e.md`.

## v0.6 — hybrid identity backend (shipped 2026-05-12)

Three identity providers: `local` (every row carries bcrypt),
`noauth` (every row `{NOAUTH}`, IdP owns identity), `hybrid`
(per-row credential ownership — rows with hash auth via passdb-sql,
rows with `{NOAUTH}` defer to chained non-SQL passdb). SCIM POST +
PATCH support both setting and releasing the credential.
CLI `user passwd --claim` / `user release` for local control. Domain
freedom: any subset of rows can be IdP-managed. Two patch releases
(v0.6.1 review-fixes cluster, v0.6.2 cf-mode mask).

## v0.7 — alias_domain CRUD + post-review hardening (shipped 2026-05-12)

`postino domain alias add/list/show/retarget/enable/disable/del` for
PostfixAdmin's `alias_domain` table (whole-domain rewrites via
postfix's `virtual_alias_domain_maps`). Six validation rules enforce
PA parity: no self-alias, no chains, both endpoints must exist, no
duplicates. SCIM PATCH /Aliases/{id} active toggle. CLI
enable/disable for domains and aliases (mailbox enable/disable
already shipped earlier). `postino check` now validates the two
`*_alias_domain_maps.cf` files conditionally on `alias_domain` row
count. Design spec:
`docs/superpowers/specs/2026-05-12-postino-v0.6-alias-domain-design.md`.

Bundled post-review hardening (5 HIGH + 9 MED findings from
2026-05-12 review):
- Atomic maildir delete — two-phase rename inside DB tx +
  post-commit rmtree; `.deleting.*` graveyard surfaced by
  `check --deep` (`maildir_artefacts` / `maildir_symlinks`).
- Hook + FS compensation ordering — DB rolls back first, FS comp
  second; eliminates phantom-row-pointing-at-deleted-maildir window.
- `.cf` priv-esc: `_CF_FORBIDDEN_BITS=0o037`; non-root cf owner
  promoted warn → error.
- postinod SCIM + Zitadel handlers wrapped in
  `anyio.to_thread.run_sync` — uvicorn event loop stays responsive.
- HMAC hex-decode entropy check — rejects half-entropy
  `openssl rand -hex 16` paste accidents.
- JWT defence-in-depth — require `iat`, configurable
  `scim_max_token_age_seconds` (default 3600s).
- Mailing-list cap TOCTOU — `MailingListService.add` runs
  validation + spool create inside one tx with `FOR UPDATE` on the
  domain row.
- `is_idp_managed` semantics uniform across all three providers.

Breaking: JWT tokens missing `iat` are rejected. Tokens older than
`scim_max_token_age_seconds` (default 1h) are rejected even when
`exp` is further out.

### v0.7.1 — release pipeline gate (shipped 2026-05-12)

CI/workflow-only patch (wheel code identical to v0.7.0 modulo
version string). Extracted reusable `verify.yml`
(`workflow_call`); `ci.yml` and `release.yml` both call it.
`build-and-publish` now `needs: verify`, so a tag push cannot ship
to PyPI when the same SHA's lint/test/postinod-e2e pipeline is red.

`scripts/check.sh` now surfaces silent skips (pytest 9 dropped the
trailing summary line so the previous regex was a no-op). Default:
yellow warning + remediation hint. `POSTINO_CHECK_STRICT=1` makes
it fail-exit; use this before tagging.

## Production hardening (anytime)

- Docker image — official runtime container, FROM python:3.13-slim
- FreeBSD port — `lang/python313` + bundled wheels for rust-built deps
- Manpage — typer auto-generates from `--help`
- pre-commit hook config — wraps `./scripts/check.sh` for contributors
- Coverage badge — codecov or local
- SLSA provenance for releases
