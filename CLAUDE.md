# Project memory — postino

This file is loaded automatically into every Claude Code session in `/srv/padmin`.
Keep it concise. Move anything verbose to `docs/`.

## What this is

`postino` (PyPI: `il-postino`) — typed Python CLI to admin a Postfix +
Dovecot mail stack on the PostfixAdmin SQL schema.

- Two src packages: `postino_core` (library) + `postino` (Typer CLI)
- Pydantic v2 strict, SQLAlchemy 2.0 reflection, Rich, passlib (bcrypt + md5_crypt + sha512_crypt)
- Constructor injection throughout; every service unit-testable in isolation
- Pluggable identity provider (`LocalProvider` shipped, `ZitadelProvider` planned for V2)

Spec: `docs/superpowers/specs/2026-05-09-postino-design.md`
README: top-level `README.md` (full usage + install + ops)
Roadmap: `docs/ROADMAP.md`

## Tech baseline

- Python **3.11+** required (`requires-python = ">=3.11"`). Wider OS support
  (FreeBSD pkg, RHEL 9, Debian 12) than 3.13. Stdlib `tomllib` lands at 3.11.
- Pyright `strict` mode — non-negotiable. Use `# type: ignore[arg-type]` only
  for SQLAlchemy `Any`-typed `RowMapping` access; everywhere else fix the type.
  Every ignore needs a `# WHY: ...` justification (enforced by
  `tests/architecture/test_type_ignore_justified.py`).
- Ruff selects: `E F W I B UP RUF SIM`. Line length 100. `target-version = "py311"`.
- Pytest with `-x -q`. Markers: `integration` (needs `POSTINO_TEST_DB_URL`), `cli`,
  `architecture` (static rule checks under `tests/architecture/`).
- `bcrypt<5` is pinned: passlib 1.7.4 reads `bcrypt.__about__` which 5.x removed.
- `./scripts/check.sh` must exit 0 before any commit. It auto-loads `.env` and runs
  ruff → lint-imports → pyright → pytest.
- `import-linter` enforces layered architecture (`postino_core < postino`) via
  contracts in `pyproject.toml`. Adding postinod will extend the layer chain.

## mlmmj integration (v0.3+)

- Target version: **mlmmj 1.3.x** (Debian 12, Ubuntu 24.04 LTS, FreeBSD ports).
- Spool root convention: `/var/spool/mlmmj` (env: `POSTINO_MLMMJ_SPOOL_DIR`).
- uid/gid convention: `mlmmj:mlmmj` (system user/group); UID/GID must
  match across `mta` and `agent` containers for shared-volume FS access.
- Subprocess wrapper pattern: `MlmmjAdapter` shells out to the bundled
  binaries — postino owns the flag surface, not the on-disk format.
  Avoids the mailman2-style format-ownership lock-in.
- List state of record: filesystem (spool dir + `control/owner` +
  `subscribers.d/`). No new SQL table.
- Per-list addressing handled at the `domain.transport='mlmmj'` layer;
  no `master.cf` editing per list.

## Develop

```sh
cd /srv/padmin
. .venv/bin/activate     # python 3.11+ venv at repo root
./scripts/check.sh       # all-green precondition for commit
```

Integration tests need a local MySQL/MariaDB schema — see `.env.example`
and the README § Development for the DDL.

## Release

```sh
# bump version in pyproject.toml
git tag vX.Y.Z
git push origin main vX.Y.Z
# CI workflow release.yml publishes to PyPI via OIDC trusted publishing.
# Manual fallback:
rm -rf dist/ && python -m build && twine check dist/* && twine upload dist/*
```

`~/.pypirc` is set up for the manual path (gitignored).

## FreeBSD production gotchas

If installing on a slimmed-down FreeBSD host:

- `/tmp` is often `noexec` — set `TMPDIR=/root/build-tmp` before pip build.
- Base clang sometimes ships incomplete intrinsic headers (e.g.
  `emmintrin.h` missing). Install `llvm19` and
  `export CC=/usr/local/llvm19/bin/clang`.
- pydantic-core needs Rust to build; FreeBSD ships no prebuilt wheel.
  After first build, cache wheels: `pip wheel --wheel-dir wheels/ .`
  Future installs use `pip install --no-build-isolation --find-links wheels/ .`
  and skip rust.

## Security notes

- `tests/fixtures/postfixadmin.sql` is `--no-data` schema only — safe to commit.
- DB credentials are NEVER stored in `postino.toml`. postino parses
  postfix's `sql-virtual_*.cf` for them; postfix is the single source of truth.

## Engineering preferences

- Pydantic-only validation, pyright strict, fail-fast.
- Commit-as-you-go: when touching system config under `/etc` or
  `/usr/local/etc` on hosts where those are git-tracked, commit before
  moving on. Don't leave the working tree dirty.
- Speak technical, skip handholding.

## Useful links

- PyPI: https://pypi.org/project/il-postino/
- GitHub: https://github.com/vjt/postino
