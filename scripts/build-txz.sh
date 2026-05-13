#!/bin/sh
# scripts/build-txz.sh — build il-postino-<version>.txz inside FreeBSD 14.
# Uses FreeBSD's binary py311-* ports for C-extension deps so pip doesn't
# try to rebuild pydantic-core / bcrypt / cryptography (which fails on
# FreeBSD because setuptools/wheel misparses SOABI "cpython-311").
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

# FreeBSD prereqs: Python, all C-ext deps from binary ports, plus rust
# as a fallback for any pure-pip deps that need compilation.
pkg install -y \
  python311 py311-pip py311-virtualenv \
  py311-pydantic py311-pydantic-settings \
  py311-bcrypt py311-cryptography py311-cffi \
  py311-typer py311-rich py311-sqlalchemy20 py311-pymysql \
  py311-passlib py311-pyjwt py311-httpx py311-uvicorn py311-anyio \
  py311-email-validator \
  rust llvm \
  mlmmj git

# Diagnostic: confirm system-site C-ext deps are visible.
/usr/local/bin/python3.11 -c "
import sys
print('Python:', sys.version)
import pydantic_core, bcrypt, cryptography
print('pydantic_core:', pydantic_core.__file__)
print('bcrypt:', bcrypt.__file__)
print('cryptography:', cryptography.__file__)
"

# Stage tree.
install -d "$STAGE/usr/local/share/postino/venv"
install -d "$STAGE/usr/local/bin"
install -d "$STAGE/usr/local/man/man1"
install -d "$STAGE/usr/local/man/man8"
install -d "$STAGE/usr/local/etc/rc.d"
install -d "$STAGE/usr/local/etc/postino"

# Build venv with --system-site-packages so it inherits the ports-installed
# C extensions. pip will not rebuild them since they're already importable.
VENV="$STAGE/usr/local/share/postino/venv"
/usr/local/bin/python3.11 -m venv --system-site-packages "$VENV"

# Use venv's python -m pip explicitly so scripts always land in $VENV/bin/,
# not in the system bin/ (the pip shim can misbehave with --system-site-packages).
"$VENV/bin/python3.11" -m pip install --upgrade pip setuptools wheel

# Install postino itself. pip resolves deps against system-site-packages
# FIRST, so any pydantic/bcrypt/cryptography already there are accepted
# (modulo version constraints). Daemon deps (litestar) are NOT in
# FreeBSD ports, so pip will fetch them from PyPI as pure-Python wheels.
"$VENV/bin/python3.11" -m pip install --no-cache-dir '.[daemon]'

# Diagnostics: list what got installed where.
echo "=== venv bin/ contents ==="
ls -la "$VENV/bin/"
echo "=== venv site-packages ==="
"$VENV/bin/python3.11" -c "import postino; print('postino at:', postino.__file__)"
"$VENV/bin/python3.11" -c "import postinod; print('postinod at:', postinod.__file__)" || echo "WARNING: postinod not importable in venv"

# Replace absolute shebangs with /usr/local prefix (BSD sed -i needs empty extension).
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

# Build the package (FreeBSD 14 default = .pkg / zstd).
pkg create -M "$WORK/manifest.json" -r "$STAGE" -o "$DIST"

echo "Built:"
ls -la "$DIST"/il-postino-*.pkg
