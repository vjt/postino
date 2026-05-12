#!/bin/sh
# scripts/build-manpages.sh — regenerate man/postino.1 from --help + supplement,
# and man/postinod.8 from the troff template with date+version substitution.
#
# Idempotent. Called by scripts/release.sh and verified in CI.
set -eu

cd "$(dirname "$0")/.."

# Fix 5: precheck that postino is on PATH.
command -v postino >/dev/null 2>&1 || {
  echo "ERROR: postino not on PATH — activate the .venv first" >&2
  exit 1
}

VERSION=$(grep -E '^version = ' pyproject.toml | sed -E 's/version = "([^"]+)"/\1/')

# Fix 4: guard against empty VERSION.
if [ -z "$VERSION" ]; then
  echo "ERROR: could not extract version from pyproject.toml" >&2
  exit 1
fi

DATE="${DATE:-$(date +%Y-%m-%d)}"

# Wrapper script: strip Rich's Unicode box-drawing before help2man sees it.
# Typer's rich_markup_mode="rich" renders ╭─ ╮ │ ╰ ╯ regardless of NO_COLOR;
# stripping here avoids ??? replacement characters in the troff output.
# Fix 3: extended to full Box Drawing block U+2500..U+257F via Python one-liner.
WRAPPER=$(mktemp /tmp/postino-help2man-wrapper.XXXXXX)
# Fix 1: trap EXIT so the tempfile is cleaned up even if help2man fails.
trap 'rm -f "$WRAPPER"' EXIT
# Fix 2: write postino output to a tempfile so failures propagate non-zero;
#         the previous pipe swallowed postino's exit code.
cat > "$WRAPPER" << 'WRAPPER_EOF'
#!/bin/sh
set -eu
tmpout=$(mktemp /tmp/postino-help.XXXXXX)
trap 'rm -f "$tmpout"' EXIT
NO_COLOR=1 TERM=dumb COLUMNS=80 postino "$@" > "$tmpout"
python3 -c "import sys; sys.stdout.write(''.join(c if not (0x2500 <= ord(c) <= 0x257F or ord(c) == 0x2014) else ('-' if ord(c) == 0x2014 else '') for c in sys.stdin.read()))" < "$tmpout"
WRAPPER_EOF
chmod +x "$WRAPPER"

# postino(1) — help2man scrapes `postino --help` and merges the supplement.
help2man --no-info --no-discard-stderr \
  --name="administer a Postfix + Dovecot mail server" \
  --section=1 \
  --version-string="postino $VERSION" \
  --include=docs/man/postino.1.h2m \
  --output=man/postino.1 \
  "$WRAPPER"

# Fix 1: $WRAPPER removal is handled by the trap above — no explicit rm -f here.

# Fix the .TH date: help2man generates locale month/year ("May 2026") instead
# of ISO-8601. Replace the third quoted field with today's date.
sed -i.bak -E "s/^(\.TH [A-Z]+ \"[0-9]+\") \"[^\"]+\" /\1 \"$DATE\" /" man/postino.1
rm -f man/postino.1.bak

# postinod(8) — substitute date and version into the troff template.
sed -e "s/@DATE@/$DATE/" -e "s/@VERSION@/$VERSION/" \
  docs/man/postinod.8.in > man/postinod.8

# Lint both.
mandoc -Tlint man/postino.1 man/postinod.8

echo "Regenerated: man/postino.1 man/postinod.8 (version=$VERSION date=$DATE)"
