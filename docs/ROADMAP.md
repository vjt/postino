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

## Production hardening (anytime)

- Docker image — official runtime container, FROM python:3.13-slim
- FreeBSD port — `lang/python313` + bundled wheels for rust-built deps
- Manpage — typer auto-generates from `--help`
- pre-commit hook config — wraps `./scripts/check.sh` for contributors
- Coverage badge — codecov or local
- SLSA provenance for releases
