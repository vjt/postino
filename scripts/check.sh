#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

# Put the venv's bin/ first on PATH so subprocess lookups (e.g.
# tests/architecture/test_layer_boundaries.py's `shutil.which("lint-imports")`)
# resolve to the venv-installed binaries instead of skipping. Without
# this, the test reported "lint-imports not installed" even though
# the binary lives at .venv/bin/lint-imports — pytest is invoked
# directly via .venv/bin/pytest without activating the venv, so $PATH
# stays the caller's untouched PATH.
PATH="$(pwd)/.venv/bin:$PATH"
export PATH

# Auto-load .env (POSTINO_TEST_DB_URL etc) so local runs hit the real DB
# instead of skipping integration tests.
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/lint-imports
.venv/bin/pyright

# Match ci.yml's zero-skip contract. -rs surfaces skip reasons so the
# tail counter below can detect host-dep guards that fired silently
# (mariadb absent, mlmmj-sub absent, etc.). check.sh is ALWAYS strict —
# any skip is a failure, no env-var loose mode. If a test legitimately
# can't run on this host, fix the host setup; if a test should never
# skip on a developer machine, demote it to integration/e2e and gate
# its CI job, not its skip.
out=$(mktemp)
trap 'rm -f "$out"' EXIT
set +e
.venv/bin/pytest tests/ -x -q -rs 2>&1 | tee "$out"
rc=$?
set -e
[ "$rc" -eq 0 ] || exit "$rc"

# Pytest 9 dropped the "N passed, M skipped" trailing summary in -q mode,
# so count `SKIPPED [N] path:line: reason` rows from -rs and sum the [N]
# multiplicities. Robust across pytest versions.
skipped=$(awk '/^SKIPPED \[/ { gsub(/[^0-9]/, "", $2); sum += $2 } END { print sum+0 }' "$out")
if [ "$skipped" -gt 0 ]; then
    printf '\n\033[31m[check.sh] %s test(s) were SKIPPED — failing (zero-skip is non-negotiable):\033[0m\n' "$skipped" >&2
    grep -E '^SKIPPED' "$out" | sort -u >&2 || true
    exit 1
fi
