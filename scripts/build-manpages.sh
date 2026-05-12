#!/bin/sh
# scripts/build-manpages.sh — regenerate man/postino.1 from --help + supplement,
# and man/postinod.8 from the troff template with date+version substitution.
#
# Idempotent. Called by scripts/release.sh and verified in CI.
set -eu

cd "$(dirname "$0")/.."

VERSION=$(grep -E '^version = ' pyproject.toml | sed -E 's/version = "([^"]+)"/\1/')
DATE=$(date +%Y-%m-%d)

# Wrapper script: strip Rich's Unicode box-drawing before help2man sees it.
# Typer's rich_markup_mode="rich" renders ╭─ ╮ │ ╰ ╯ regardless of NO_COLOR;
# stripping here avoids ??? replacement characters in the troff output.
WRAPPER=$(mktemp /tmp/postino-help2man-wrapper.XXXXXX)
cat > "$WRAPPER" << 'WRAPPER_EOF'
#!/bin/sh
NO_COLOR=1 TERM=dumb COLUMNS=80 postino "$@" | sed 's/[╭╮╰╯│─]//g'
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

rm -f "$WRAPPER"

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
