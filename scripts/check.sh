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
.venv/bin/pyright
.venv/bin/pytest tests/ -x -q
