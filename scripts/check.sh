#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

ruff check .
ruff format --check .
pyright
pytest tests/ -x -q
