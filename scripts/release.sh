#!/bin/sh
# scripts/release.sh — bump version, regenerate derived files, commit, tag.
# Does NOT push. Operator inspects, then `git push origin main vX.Y.Z`.
#
# Usage: ./scripts/release.sh 0.8.0
set -eu

cd "$(dirname "$0")/.."

v=${1:-}
case "$v" in
  ''|*[!0-9a-z.-]*) echo "usage: $0 <X.Y.Z[-rc.N]>" >&2; exit 1 ;;
esac

# 0. Working tree must be clean (apart from untracked specs/plans).
if [ -n "$(git status --porcelain | grep -v '^?? docs/superpowers/')" ]; then
  echo "ERROR: working tree has uncommitted changes; commit or stash first" >&2
  git status --short
  exit 1
fi

# 1. Bump pyproject.toml.
sed -i.bak -E "s/^version = \"[^\"]+\"/version = \"$v\"/" pyproject.toml
rm -f pyproject.toml.bak

# 2. Regenerate manpages with new version.
. .venv/bin/activate
./scripts/build-manpages.sh

# 3. Refresh CHANGELOG.md from current HEAD up to but not including v$v.
git-cliff --tag "v$v" -o CHANGELOG.md

# 4. Bump debian/changelog stub with an RFC-2822 timestamp.
new_entry=$(cat <<EOF
il-postino ($v-1) unstable; urgency=medium

  * Release $v. See CHANGELOG.md for details.

 -- Marcello Barnaba <vjt@openssl.it>  $(date -R)

EOF
)
{ printf '%s\n' "$new_entry"; cat debian/changelog; } > debian/changelog.new
mv debian/changelog.new debian/changelog

# 5. Architecture self-check (manpage drift, lint).
./scripts/check.sh

# 6. Show the diff for human inspection.
echo
echo "=== Diff for v$v release ==="
git diff --stat
echo

printf "Tag v%s? Press Enter to commit + tag, ^C to abort: " "$v"
read _confirm

# 7. Commit + tag.
git add pyproject.toml CHANGELOG.md debian/changelog man/postino.1 man/postinod.8
git commit -m "release: $v

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git tag "v$v"

echo
echo "Tagged v$v. Push with:"
echo "  git push origin main v$v"
