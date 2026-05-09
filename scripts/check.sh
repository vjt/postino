#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pyright
.venv/bin/pytest tests/ -x -q
