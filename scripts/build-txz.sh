#!/bin/sh
# scripts/build-txz.sh — build il-postino-<version>.txz inside a FreeBSD 14
# environment. Designed to run inside vmactions/freebsd-vm@v1 in CI; can
# also run on a FreeBSD dev box.
set -eu

cd "$(dirname "$0")/.."

VERSION=$(grep -E '^version = ' pyproject.toml | sed -E 's/version = "([^"]+)"/\1/')
if [ -z "$VERSION" ]; then
  echo "ERROR: could not extract version from pyproject.toml" >&2
  exit 1
fi

WORK=$(pwd)/pkg/work
STAGE=$WORK/stage
DIST=$(pwd)/dist

rm -rf "$WORK"
mkdir -p "$STAGE" "$DIST"

# FreeBSD prereqs.
pkg install -y python311 py311-pip py311-virtualenv mlmmj || true

# Stage tree.
install -d "$STAGE/usr/local/share/postino/venv"
install -d "$STAGE/usr/local/bin"
install -d "$STAGE/usr/local/man/man1"
install -d "$STAGE/usr/local/man/man8"
install -d "$STAGE/usr/local/etc/rc.d"
install -d "$STAGE/usr/local/etc/postino"

# Build venv inside the staging dir.
/usr/local/bin/python3.11 -m venv "$STAGE/usr/local/share/postino/venv"
"$STAGE/usr/local/share/postino/venv/bin/pip" install --no-cache-dir .

# Replace absolute shebangs with /usr/local prefix (BSD sed -i needs the empty extension).
find "$STAGE/usr/local/share/postino/venv/bin" -type f -exec \
  sed -i '' "s|$STAGE/usr/local|/usr/local|g" {} +

# Entry-point wrappers.
ln -sf /usr/local/share/postino/venv/bin/postino  "$STAGE/usr/local/bin/postino"
ln -sf /usr/local/share/postino/venv/bin/postinod "$STAGE/usr/local/bin/postinod"

# Manpages.
cp man/postino.1  "$STAGE/usr/local/man/man1/"
cp man/postinod.8 "$STAGE/usr/local/man/man8/"
gzip -f "$STAGE/usr/local/man/man1/postino.1"
gzip -f "$STAGE/usr/local/man/man8/postinod.8"

# rc(8) script.
install -m 0755 pkg/postinod.rc "$STAGE/usr/local/etc/rc.d/postinod"

# Sample config.
install -m 0644 pkg/postino.toml.sample "$STAGE/usr/local/etc/postino/postino.toml.sample"

# Manifest with version substituted.
sed "s/@VERSION@/$VERSION/" pkg/manifest.json.in > "$WORK/manifest.json"

# Build the .txz.
pkg create -M "$WORK/manifest.json" -r "$STAGE" -o "$DIST"

echo "Built:"
ls -la "$DIST"/*.txz
