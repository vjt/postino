#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

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
# (mariadb absent, mlmmj-sub absent, etc.). Default behaviour: warn
# loudly. Set POSTINO_CHECK_STRICT=1 in your env (or in .env) before a
# release to enforce zero-skip, matching CI.
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
    printf '\n\033[33m[check.sh] %s test(s) were SKIPPED:\033[0m\n' "$skipped" >&2
    grep -E '^SKIPPED' "$out" | sort -u >&2 || true
    printf '\033[33m[check.sh] Local skips are usually missing host deps (mariadb running? mlmmj-sub on PATH?).\033[0m\n' >&2
    printf '\033[33m[check.sh] CI enforces zero-skip; before tagging a release run POSTINO_CHECK_STRICT=1 ./scripts/check.sh.\033[0m\n' >&2
    if [ "${POSTINO_CHECK_STRICT:-0}" = "1" ]; then
        printf '\033[31m[check.sh] POSTINO_CHECK_STRICT=1 and %s skip(s) detected — failing.\033[0m\n' "$skipped" >&2
        exit 1
    fi
fi
