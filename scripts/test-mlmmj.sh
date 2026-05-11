#!/bin/sh
# Run the mlmmj-dependent test suites inside a docker container that has
# mlmmj installed. Lets devs without mlmmj on the host exercise the full
# integration + e2e CLI suites with one command.
#
# In scope:
#   - tests/integration/test_mailing_list_service.py (MlmmjAdapter direct)
#   - tests/e2e_cli/test_cli_list_e2e.py             (postino list … subprocess)
#
# Out of scope (already runs in a compose stack):
#   - tests/postinod_e2e/lists/test_list_e2e.py — runs via the lists CI job.
#
# DB: the container reaches the host's mariadb via --network=host (Linux).
# On Mac/Windows replace POSTINO_TEST_DB_URL's host with host.docker.internal.

set -eu

cd "$(dirname "$0")/.."

if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

: "${POSTINO_TEST_DB_URL:?POSTINO_TEST_DB_URL must be set (see .env.example)}"

IMG=postino-mlmmj-tests
DOCKERFILE=tests/postinod_e2e/lists/Dockerfile.agent

echo ">> building $IMG (cached layers reused)…"
docker build -t "$IMG" -f "$DOCKERFILE" .

echo ">> running mlmmj-dependent tests inside $IMG…"
# --network=host: hit the host's mariadb without container/host hostname juggling.
# Bind-mount tests/ so local edits to test files don't require image rebuild.
# Bind-mount src/  so the installed package picks up source edits via the
# editable layout (pip already installed the project; we override the importable
# tree by prepending it on PYTHONPATH).
exec docker run --rm \
    --network=host \
    -v "$PWD/tests:/app/tests:ro" \
    -v "$PWD/src:/app/src:ro" \
    -e POSTINO_TEST_DB_URL="$POSTINO_TEST_DB_URL" \
    -e PYTHONPATH=/app/src \
    "$IMG" \
    pytest -x -q \
        tests/integration/test_mailing_list_service.py \
        tests/e2e_cli/test_cli_list_e2e.py \
        "$@"
