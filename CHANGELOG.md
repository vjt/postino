# Changelog

All notable changes to `il-postino` are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and is generated automatically by [git-cliff](https://git-cliff.org) from
commit subjects on every tag.

## [0.10.2] - 2026-05-14

### Fixed

- **`postino schema migrate` no longer crashes on fresh deploys.** The
  root `_entry` callback called `build_services` →
  `reflect_schema(only=_REQUIRED_TABLES)` for every subcommand, which
  failed with `InvalidRequestError: Could not reflect: requested
  table(s) not available in Engine(...): (routes)` whenever the routes
  table didn't yet exist — i.e. precisely when the operator ran the
  bootstrap command meant to create it. `_entry` now skips
  `build_services` when `ctx.invoked_subcommand == "schema"`, letting
  schema commands fall through to their own raw-engine bootstrap.
  Reported by sibling Claude on `/srv/olografix` (athena rollout) with
  the live trace; previous workaround was applying the routes DDL
  manually via MariaDB root.

### Changed

- **Postfix sql-virtual_domain_maps.cf renamed to sql-virtual_domains.cf.**
  Postfix's parameter for the domain-membership lookup is
  `virtual_mailbox_domains` (plural noun). The `_maps` suffix
  convention is reserved for recipient→target lookups (mailbox_maps,
  alias_maps); domain membership is yes/no, so the bare plural
  `sql-virtual_domains.cf` is the right name and the one PostfixAdmin
  upstream uses. Existing deployments with the legacy singular
  `sql-virtual_domain_maps.cf` continue to work — postino accepts the
  legacy file and emits a deprecation `warn` finding instead of
  failing. Operators can rename at their leisure; the legacy fallback
  will be removed in a future release. Reported by sibling Claude on
  `/srv/olografix` (athena rollout) where the file on disk was already
  symlinked to work around the mismatch.

### Tooling

- `scripts/check.sh` is now always strict (no `POSTINO_CHECK_STRICT`
  env-var gate). Any skipped test fails the script. Matches the CI
  invariant; no more "loose locally, strict in CI" drift.
- `scripts/check.sh` prepends `$(pwd)/.venv/bin` to `PATH` so the
  architecture test's `shutil.which("lint-imports")` finds the
  venv-installed binary instead of silently skipping.

## [0.10.1] - 2026-05-14

### Fixed

- **Phantom `mlmmj-help` binary removed from spec, code, and master.cf
  template.** mlmmj 1.3+ (Debian 12 1.3.0-4, Debian 13 1.5.2-1, FreeBSD
  ports) ships no `mlmmj-help` binary; the v0.10.0 spec referenced it
  in the `master.cf` snippet, in `_REQUIRED_MASTER_CF_PIPES`, and in
  `_MLMMJ_SUFFIXES` (one of the five per-list `routes` rows). Postfix
  attempts to invoke a nonexistent binary on every `list-help@` request,
  producing an MTA error and no auto-reply. The bug shipped because the
  e2e test for help routing was `@pytest.mark.skip`'d with a misleading
  reason ("no configured help-text") that masked the missing binary.
  Reported by sibling Claude on `/srv/olografix` (athena rollout) via
  `cc-talk` cross-pane channel.

### Changed

- **Help requests now ride plus-addressing**: send to `list+help@domain`
  instead of `list-help@domain`. The priority-50 catchall route
  `^list(\+.+)?@dom$` already maps `+ext` into `mlmmj-receive`, which
  carries `-e ${extension}` in the master.cf snippet and emits the
  matching `text/listcontrol-help` template (or `+faq`, `+get-N`,
  `+subscribe`, `+owner`, etc.). This is the canonical mlmmj 1.3+
  interface; no new wiring is required if you already deployed v0.10.0.
- `postino list add` now writes **4** rows to the `routes` table per
  list (down from 5). Existing v0.10.0 deployments should clean up the
  obsolete row:
  ```sql
  DELETE FROM routes WHERE transport = 'mlmmj-help:';
  ```
  Optionally drop the `mlmmj-help` block from `/etc/postfix/master.cf`
  (it does nothing — the transport is no longer referenced).
- `postino check` validates **4** master.cf pipe blocks (not 5).
- New e2e test `test_help_routing_emits_auto_reply` exercises the
  `list+help@` path end-to-end: catcher must receive an auto-reply
  addressed back to the requester. **Not skipped — runs in CI.**

## [0.10.0] - 2026-05-14

### BREAKING

- **mlmmj listdir layout changed.** v0.3-v0.9 used `<spool>/<full-email>/`;
  v0.10 uses `<spool>/<domain>/<localpart>/`. Operator migration:
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

- **`DomainTransport.MLMMJ` removed.** `postino domain add --transport
  mlmmj` is no longer accepted. Lists are routed per-list via the new
  `routes` table; `domain.transport` continues to drive non-list mail
  (`virtual`, `lmtp`, `relay`). Existing `domain.transport='mlmmj'` rows
  are operator-side cleanup: `UPDATE domain SET transport='virtual'
  WHERE transport='mlmmj'`.

- **New required SQL table `routes`.** Run once before starting postinod
  or any `postino list` command:
  ```sh
  postino schema migrate
  ```
  The DDL is included in the postino package and applied idempotently.
  Note: postino startup now requires this table — `reflect_schema`
  loads `_REQUIRED_TABLES = (..., "routes")`. Pre-v0.10 deployments
  will fail-fast with `ConfigError` until the migration is applied.

- **Postfix `transport_maps` and `recipient_delimiter` requirements.**
  `main.cf` MUST now declare:
  ```
  transport_maps = mysql:/etc/postfix/sql-routes.cf, mysql:/etc/postfix/sql-virtual_transport_maps.cf
  recipient_delimiter = +-
  ```
  And `master.cf` MUST carry 5 pipe service blocks (`mlmmj-receive`,
  `mlmmj-bounce`, `mlmmj-sub`, `mlmmj-unsub`, `mlmmj-help`). See
  `docs/postino-mlmmj.md` for the canonical snippets.

### Added

- New `routes` SQL table — postino-managed routing patterns.
- `postino schema migrate` — apply v0.10 routes DDL idempotently.
- `postino list add` now writes 5 routes rows + 1 `-owner` alias row.
- `postino list rm` cleans them up.
- `postino check` validates `transport_maps`, `recipient_delimiter`,
  `master.cf` pipe entries, and `-owner` alias presence.
- Shared-domain mailing lists supported (`soci@example.org` alongside
  `alice@example.org` mailbox).

### Changed

- `MlmmjAdapter._listdir` composes `<spool>/<domain>/<localpart>/`.
- `MlmmjAdapter.list_all` walks the two-level layout.
- `MailingListService` constructor takes a `routes: RoutesRepository`.
- `DomainService` no longer takes `mlmmj_enabled`.

### Removed

- `DomainTransport.MLMMJ` enum value.
- `MailingListService._validate_domain_is_mlmmj`.
- `DomainService` mlmmj-transport refusal.

## [0.8.5] — 2026-05-13

### CI
- Pin deb daemon-extras via requirements; force txz format; smoke -x
## [0.8.4] — 2026-05-13

### Fixed
- Add postinod console_script entry; drop broken staged smoke

### Release
- 0.8.4
## [0.8.3] — 2026-05-13

### Packaging
- Install daemon extras + use venv-python for pip *(`deb,txz`)*

### Release
- 0.8.3
## [0.8.2] — 2026-05-13

### Packaging
- Re-enable FreeBSD build using pkg binary deps *(`txz`)*
- Install manpages and rename systemd unit to postinod.service *(`deb`)*
- Remove orphan il-postino.service file *(`deb`)*

### Release
- 0.8.2
## [0.8.1] — 2026-05-13

### CI
- Fix deb smoke + replace broken changelog action; drop txz to v0.9

### Release
- 0.8.1
## [0.8.0] — 2026-05-13

### CI
- Add git-cliff config and seed CHANGELOG.md
- Harden build-manpages.sh — trap, fail-fast, version guard
- Add opt-in pre-commit hook running scripts/check.sh
- Add scripts/release.sh — bump version, regen, tag
- Upload coverage to Codecov via OIDC
- Extend release.yml with changelog, manpages, deb, txz, gh-release
- Relax release.sh precondition to ignore all untracked files
- Drop arm64 from deb matrix; v0.8.0 startup_failure root cause
- Grant verify job-call id-token + contents permissions
- Install help2man + mandoc in verify test job

### Docs
- Add postinod(8) manpage template and rendering
- Correct postinod(8) env vars and SYNOPSIS
- Add help2man supplement for postino(1)
- Remove misleading Debian path claim from postino(1) FILES
- Correct POSTINO_CONFIG semantics in postino(1)
- Add build-manpages.sh and committed postino(1)
- Add codecov badge and .deb/.txz install instructions

### Misc
- Drop reconcile stub, enable typer shell completion

### Packaging
- Add Debian packaging skeleton with dh-virtualenv *(`deb`)*
- Add scripts/build-deb.sh local Docker-based builder *(`deb`)*
- Add FreeBSD pkg-create skeleton and build-txz.sh *(`txz`)*

### Release
- 0.8.0

### Tests
- Guard manpage drift and lint cleanliness *(`architecture`)*
- Fold in Task 5 minor review nits
## [0.7.1] — 2026-05-12

### CI
- Gate release.yml on verify pipeline; check.sh detects skips

### Fixed
- Seed mlmmj-transport domain with mlmmj_enabled=True *(`test`)*
- JWT token in postinod_e2e SCIM conftest must include iat *(`e2e`)*

### Release
- 0.7.1 — release pipeline gate
## [0.7.0] — 2026-05-12

### Added
- Add RuleViolationError for cycle/self-alias guards *(`core`)*
- Add AliasDomain Pydantic model *(`core`)*
- AliasDomainService scaffold + list/get *(`core`)*
- AliasDomainService.add with strict validation *(`core`)*
- AliasDomainService delete + set_status + retarget *(`core`)*
- Wire AliasDomainService into ServicesBundle *(`core`)*
- DomainService.set_status *(`core`)*
- AliasService.set_status *(`core`)*
- Postino domain alias add/list/show/del *(`cli`)*
- Postino domain alias enable/disable/retarget *(`cli`)*
- Postino domain enable/disable *(`cli`)*
- Postino alias enable/disable *(`cli`)*
- Conditional alias_domain cf-file policy *(`check`)*
- PATCH /Aliases/{id} with active replace op *(`scim`)*

### Docs
- Alias_domain CRUD, enable/disable parity, cf-files note *(`v0.6`)*

### Fixed
- Race-safe add + IntegrityError translation + audit data parity *(`core`)*

### Misc
- Ruff format on test_models.py *(`test`)*
- Merge pull request #1 from vjt/postino-v0.6-alias-domain

postino v0.6: alias_domain CRUD + enable/disable parity
- Close 5 HIGH + 9 MED findings from 2026-05-12 review *(`v0.6+`)*

### Release
- 0.7.0 — alias_domain CRUD + post-review hardening

### Tests
- Regression guard for Users PATCH active round-trip *(`scim`)*
## [0.6.2] — 2026-05-12

### Fixed
- Cf-mode mask conflated group-read with others-read *(`check`)*
## [0.6.1] — 2026-05-12

### Misc
- Bump version to 0.6.1 (review-fixes cluster: transactional + daemon hardening)

### Refactored
- Transactional integrity — FS-inside-tx + mlmmj atomicity *(`core`)*
- Harden network surface — body cap, replay dedup, error scrub *(`postinod`)*
- Hide {NOAUTH} sentinel behind IdentityProvider *(`core`)*
- Secret hygiene — scrub env, sanitize audit data, cf-mode check
- Fail loud when audit_context is missing *(`postinod`)*
- Reliability nits — reflect assertion, mlmmj guard, fork-safe drop
## [0.6.0] — 2026-05-11

### Added
- Add HYBRID identity backend enum value *(`core`)*
- Add release_identity + supports_release_to_noauth to IdentityProvider Protocol *(`core`)*
- Satisfy release_identity on Local + NoAuth providers *(`core`)*
- Add HybridProvider for per-row credential ownership *(`core`)*
- Add MailboxService.release_identity *(`core`)*
- Dispatch HYBRID backend in settings + bundle *(`core`)*
- Require non-SQL passdb under HYBRID identity backend *(`core`)*
- Add write-only password field to ScimUser *(`postinod`)*
- SCIM POST /Users honours password attribute *(`postinod`)*
- SCIM PATCH password supports set / release (Okta + Azure dialects) *(`postinod`)*
- User passwd --claim flag for IdP→SQL auth transition *(`cli`)*
- User release command (SQL→IdP auth transition) *(`cli`)*

### Docs
- Clarify release_identity audit-row semantics on sentinel
- Hybrid identity backend — three-mode stanza + dovecot passdb chain example

### Misc
- Combine nested with in test_local_provider (ruff SIM117)
- Align private-usage suppression with codebase convention
- Align SCIM password-release audit verb with mailbox.release *(`postinod`)*
- Bump version to 0.6.0 (hybrid identity backend)

### Refactored
- Rename + rescope identity guard to NOAUTH-only *(`postinod`)*
- Drop redundant mailbox.get in passwd; rely on is_idp_managed NotFoundError *(`cli`)*

### Tests
- Integration coverage for SCIM password lifecycle (POST + PATCH) *(`postinod`)*
- Assert audit verbs + document shared-DB contract in SCIM password tests *(`postinod`)*
- AST guard — ScimUser password is write-only + never set on responses *(`arch`)*
- AST guard — Zitadel handlers never construct MailboxCreate with credentials *(`arch`)*
## [0.5.0] — 2026-05-11

### Added
- Portable adapter create — direct fs layout *(`mlmmj`)*
- Full lists delivery e2e in docker compose *(`e2e`)*

### Misc
- Post-review hardening — fs/mlmmj/config/cli + CI 0-skip enforcement

### Release
- 0.5.0 + postinod-e2e-lists CI job
## [0.4.0] — 2026-05-11

### Added
- Add SCIM list endpoints for Users, Aliases, Domains *(`postinod`)*
- Harden HMAC + JWKS auth surface (v0.4 Task 1) *(`postinod`)*
- Atomic postino + postinod dual-row writer (v0.4 Task 2) *(`audit`)*
- Meta block + introspected /Schemas + scim2-models e2e (v0.4 Task 4) *(`scim`)*
- Startup guard + dovecot passdb probe + real conformance (v0.4 Task 5) *(`noauth`)*

### Docs
- Clarify Zitadel surface is inbound-only; drop ZitadelProvider *(`claude`)*

### Fixed
- Atomic FS+DB delete --force; keep_maildir flag (v0.4 Task 3) *(`domain`)*
- Wrap enable/disable/quota in MailctlError handler + exit-code test (v0.4 Task 6) *(`cli`)*
- Zitadel router uses dynamic created_at to clear the 24h replay window *(`tests`)*
- Scim compose HMAC secret meets v0.4 entropy floor *(`e2e`)*

### Release
- 0.4.0 — hardening + v0.4 Task 1-6
## [0.3.0] — 2026-05-10

### Added
- Add DomainTransport.MLMMJ for mlmmj-routed domains *(`enums`)*
- Add MlmmjError + CLI exit-code mapping (9) *(`errors`)*
- Add MailingList + MailingListCreate *(`models`)*
- Add mlmmj_spool_dir/uid/gid settings *(`config`)*
- MlmmjAdapter.create — subprocess wrapper *(`adapters`)*
- MlmmjAdapter.append_owner with flock *(`adapters`)*
- MlmmjAdapter.delete via shutil.rmtree *(`adapters`)*
- MlmmjAdapter.subscribe / unsubscribe *(`adapters`)*
- MlmmjAdapter.get + list_all *(`adapters`)*
- MailingListService.add — validate + create + compensate *(`services`)*
- MailingListService subscribe/unsubscribe + get *(`services`)*
- MailingListService.delete with subscriber-count guard *(`services`)*
- MailingListService.list_all *(`services`)*
- Wire MailingListService when mlmmj_spool_dir is set *(`bundle`)*
- Postino list add/sub/unsub/show/ls/rm *(`cli`)*

### Docs
- Postino-mlmmj.md operator notes + ROADMAP v0.3 status

### Misc
- Bump to 0.3.0; document mlmmj integration in CLAUDE.md

### Refactored
- Post-merge cleanup — path guard, exists(), audit-first delete, enum compare *(`mlmmj`)*

### Tests
- Strip :port from cf `hosts =` field
- Subprocess CLI e2e for postino list *(`e2e_cli`)*
- Docker compose e2e for postino list *(`postinod_e2e`)*
## [0.2.0] — 2026-05-10

### CI
- Install daemon extras + add postinod-e2e-scim job

### Misc
- PR-B0 — package scaffold, deps, audit constants
- PR-B1 — PostinodSettings (toml + env, fail-fast HMAC)
- PR-B1.1 — review fixes for PostinodSettings
- PR-B2 — Litestar app skeleton + /healthz, /readyz
- PR-B2.1 — review fixes for app skeleton
- PR-B3 — HmacVerifier (HMAC-SHA256, constant-time)
- PR-B3.1 — review fixes for HmacVerifier
- PR-B4 — JwksCache (TTL + force-refresh + stale-on-failure)
- PR-B4.1 — review fixes for JwksCache
- PR-B5 — JwtVerifier (RS256, JWKS-keyed, iss/aud/exp validation)
- PR-B5.1 — review fixes for JwtVerifier
- PR-B6 — Zitadel event payload models
- PR-B6.1 — review fixes for Zitadel payload models
- PR-B7 — Zitadel event dispatch table
- PR-B8 — Zitadel events router + integration tests
- PR-B8.1 — review fixes for Zitadel events router
- PR-B9 — SCIM 2.0 resource models (User, Alias, Error, PatchOp, ListResponse)
- PR-B9.1 — review fixes (frozen=True, optional Resources, ScimAlias schema validator)
- PR-B10 — SCIM error mapping per RFC 7644 §3.12
- PR-B11 — SCIM /Users router (POST/GET/PATCH/DELETE)
- PR-B11.1 — review fixes for SCIM /Users router
- PR-B12 — SCIM /Aliases router (postino custom resource)
- PR-B13 — SCIM discovery (ServiceProviderConfig, ResourceTypes, Schemas)
- PR-B14 — production build_app() + __main__ entrypoint
- PR-B15 — SCIM e2e (Docker Compose, RFC 7644 sequence)
- Filter PostfixAdmin's `ALL` pseudo-domain row
- Fix HookRunner postcreation contract (USERNAME DOMAIN MAILDIR QUOTA)
- Silence passlib bcrypt warning at CLI surface
- Shim bcrypt.__about__ so passlib 1.7.4 stops trapping

### Release
- 0.2.0 — postinod V2 GA + production fixes

### Tests
- Add subprocess-driven e2e CLI test suite (tests/e2e_cli/)
- Cover all postino write commands; seed complete PA-style fixture
## [0.1.2] — 2026-05-10

### Docs
- Postino-stack infra design — parallel classic mail stack on athena *(`spec`)*
- Postino-stack — bring agent container in scope, add Spamhaus DQS *(`spec`)*

### Misc
- PR-A0 — architecture tests, import-linter, Python 3.11 floor
- Revert "tooling: PR-A0 — architecture tests, import-linter, Python 3.11 floor"

This reverts commit 3d23468a60ba58bbef3ef457dfe7483ac40fad4f.
- Revert "docs(spec): postino-stack — bring agent container in scope, add Spamhaus DQS"

This reverts commit 95acdc68b9a17022e50f6180138070c938a09cc9.
- Reapply "tooling: PR-A0 — architecture tests, import-linter, Python 3.11 floor"

This reverts commit 4be8c271b330137d1c210021b09c187d20f066cd.
- Revert "docs(spec): postino-stack infra design — parallel classic mail stack on athena"

This reverts commit cf3c5ff6a6fd9928116a2bd80076b61237ce8200.
- PR-A1 — SecretStr credentials, no --password on argv
- PR-A2 — maildir-first ordering, hook timeout, alias capacity
- PR-A3 — DomainService.delete cascade with --force
- PR-A4 — consistency.py becomes a real drift detector
- PR-A5 — NoAuth backend, optional MailboxCreate creds, dispatch
- PR-A6 — kill DB-URL-OVERRIDE footgun, move Renderer, extract StatusService
- PR-A7.5 — UTC-aware default clock in cli.py
- PR-A7.3 — verify_password ConfigError fallback for symmetry
- PR-A7.7 — placeholder [cli] / [daemon] groups in pyproject
- PR-A7.2 — UPSERT _insert_quota_row to zero stale counters
- PR-A7.4 — LocalProvider bumps mailbox.modified on password change
- PR-A7.10 — DomainTransport carries protocol only
- PR-A7.6 — translate MySQL deadlock / lock-wait timeout to DeadlockError
- PR-A7.9 — warn on orphan-alias goto on delete
- PR-A7.11 — write postino.* events to PA log table
- PR-A7.8 — settings-precedence integration tests

### Release
- 0.1.2 — postinod-prep cleanup
## [0.1.1] — 2026-05-10

### Added
- Wire TOML config loading via pydantic-settings *(`config`)*
- Friendly error on missing/invalid config *(`cli`)*

### Docs
- Full README rewrite, CLAUDE.md project memory, GitHub Actions
- Add cover image *(`readme`)*
- Add CLAUDE.md and ROADMAP.md (public-safe)

### Fixed
- Strip mysqldump warnings from sql fixture, harden conftest *(`test`)*
- Conftest skips replication/GTID SET stmts (BINLOG ADMIN) *(`test`)*
- Conftest only replays DDL statements from schema dump *(`test`)*
- Pass --json before subcommand (typer top-level option) *(`test`)*
- Use .example.org instead of .test (reserved TLD) *(`test`)*

### Misc
- Scripts/ci-watch.sh — poll a CI run until completion

### Release
- 0.1.1 — TOML config, friendlier errors, CI

### Tests
- Integration tests run by default — auto-load .env, drop stale schema
## [0.1.0] — 2026-05-10

### Added
- Enums for mailbox status, password scheme, quota unit, transport, backend *(`core`)*
- MailctlError hierarchy with 7 subclasses *(`core`)*
- Parse_quota / format_quota with binary suffixes *(`core`)*
- Password hash/verify with {scheme}prefix for dovecot *(`core`)*
- Pydantic models — Mailbox, MailboxCreate, Usage, Alias, Domain *(`core`)*
- Config parser for postfix sql-*.cf + PostinoSettings *(`core`)*
- SQLAlchemy engine factory + PA schema reflection *(`core`)*
- IdentityProvider Protocol — create/set/delete identity *(`core`)*
- LocalProvider — writes/updates mailbox.password in tx *(`core`)*
- FilesystemAdapter + HookRunner with path-traversal guard *(`core`)*
- MailboxService.add — atomic create with FS rollback *(`core`)*
- MailboxService.{delete,list,set_password,set_status,set_quota} *(`core`)*
- AliasService — add/get/delete/list *(`core`)*
- DomainService — CRUD on PA domain table *(`core`)*
- QuotaService — read quota2 usage rows *(`core`)*
- ServicesBundle wiring (build_services factory) *(`core`)*
- Renderer — Rich tables + JSON output *(`core`)*
- Postino check — read-only consistency validator *(`core`)*
- Postino user subcommands (add/del/list/show/passwd/enable/disable/quota) *(`cli`)*
- Postino alias subcommands (add/del/list) *(`cli`)*
- Postino domain subcommands (add/del/list) *(`cli`)*
- Postino quota show *(`cli`)*
- Postino check — human findings, exit 4 on failure *(`cli`)*
- Postino status — row-count snapshot *(`cli`)*

### Docs
- Usage + configuration + development sections *(`readme`)*

### Fixed
- Quota show — use NotFoundError per spec *(`cli`)*

### Misc
- Initial commit: postino design spec

postino is a typed Python CLI for administering Postfix + Dovecot
mail servers backed by the PostfixAdmin SQL schema. Pluggable
identity backend (local password column today; Zitadel/SCIM as a
future deployment mode).

This commit ships the design spec, MIT license, README, and
.gitignore — no implementation code yet. Spec drives the next
session's writing-plans pass.
- Ignore .worktrees/
- Pyproject + check.sh — typer/pydantic/sqlalchemy/passlib deps
- Package skeleton (postino_core + postino + tests tree)
- Pyright strict cleanup + final check.sh green for MVP
- Ruff format pass across project
- Postino-mvp — full MVP implementation (28 tasks)

### Release
- Bump to 0.1.0, py3.13+ only, pin bcrypt<5

### Tests
- Conftest + PA schema dump from m42

