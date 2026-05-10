#!/bin/sh
# Watch a GitHub Actions CI run until it completes.
# Usage:
#   scripts/ci-watch.sh                  # latest run on current branch
#   scripts/ci-watch.sh <RUN_ID>         # specific run
#   scripts/ci-watch.sh --logs <RUN_ID>  # also dump failed logs at end
set -eu

with_logs=0
if [ "${1:-}" = "--logs" ]; then
    with_logs=1
    shift
fi

run_id="${1:-}"
if [ -z "$run_id" ]; then
    branch=$(git rev-parse --abbrev-ref HEAD)
    run_id=$(gh run list --branch "$branch" --limit 1 --json databaseId -q '.[0].databaseId')
    [ -z "$run_id" ] && { echo "no runs found on $branch" >&2; exit 1; }
    echo "watching latest run on $branch: $run_id"
fi

prev=""
while :; do
    s=$(gh run view "$run_id" --json status,conclusion 2>&1)
    status=$(echo "$s" | grep -oE '"status":"[^"]+"' | cut -d'"' -f4)
    jobs=$(gh run view "$run_id" 2>&1 | grep -E '^[[:space:]]*[X✓*][[:space:]]')
    if [ "$jobs" != "$prev" ]; then
        printf '\n--- %s ---\n%s\n' "$(date +%H:%M:%S)" "$jobs"
        prev="$jobs"
    fi
    if [ "$status" = "completed" ]; then
        concl=$(echo "$s" | grep -oE '"conclusion":"[^"]+"' | cut -d'"' -f4)
        echo
        echo "===== overall: $concl ====="
        if [ "$with_logs" -eq 1 ] && [ "$concl" != "success" ]; then
            echo "===== failed logs ====="
            gh run view "$run_id" --log-failed
        fi
        case "$concl" in success) exit 0 ;; *) exit 1 ;; esac
    fi
    sleep 15
done
