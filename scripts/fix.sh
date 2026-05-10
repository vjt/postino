#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

.venv/bin/ruff format .
.venv/bin/ruff check --fix .
