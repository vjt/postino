#!/bin/sh
# scripts/build-txz.sh — build il-postino-<version>.pkg inside FreeBSD 14.
# Hermetic venv + Rust toolchain: pip builds pydantic-core, bcrypt and
# cryptography from sdist into the venv. Only python311 + mlmmj remain
# external runtime deps.
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

# FreeBSD prereqs: Python + Rust toolchain + headers needed by pip to
# build pydantic-core, bcrypt, cryptography wheels inside the venv.
# We do NOT install py311-pydantic / py311-pydantic-settings because the
# 1.x line in ports conflicts with the 2.x line (py311-pydantic2), and
# pip+rust rebuilds the 2.x stack into the venv anyway.
pkg install -y \
  python311 py311-pip py311-virtualenv \
  rust llvm \
  pkgconf libffi openssl \
  mlmmj git

# Diagnostic: confirm Python + Rust are available.
/usr/local/bin/python3.11 --version
/usr/local/bin/cargo --version || true

# Stage tree.
install -d "$STAGE/usr/local/share/postino/venv"
install -d "$STAGE/usr/local/bin"
install -d "$STAGE/usr/local/man/man1"
install -d "$STAGE/usr/local/man/man8"
install -d "$STAGE/usr/local/etc/rc.d"
install -d "$STAGE/usr/local/etc/postino"

# Build a hermetic venv (no --system-site-packages). All Python deps,
# including the Rust-compiled pydantic-core / bcrypt / cryptography, get
# bundled into the venv by pip below.
VENV="$STAGE/usr/local/share/postino/venv"
/usr/local/bin/python3.11 -m venv "$VENV"

# Use venv's python -m pip explicitly so scripts always land in $VENV/bin/,
# not in the system bin/ (the pip shim can misbehave with --system-site-packages).
"$VENV/bin/python3.11" -m pip install --upgrade pip setuptools wheel

# Install postino + daemon extras. pip builds pydantic-core, bcrypt,
# cryptography from sdists via the Rust toolchain installed above.
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

# pkg create with -M alone does NOT scan the staged tree for files; it
# only packages what's listed in the manifest's "files" array or in -p
# plist. Generate the plist from the staged tree so every file under
# $STAGE ends up in the package.
(cd "$STAGE" && find . \( -type f -o -type l \) -print | sed 's|^\.||') > "$WORK/plist"
echo "=== plist (first 20 entries) ==="
head -20 "$WORK/plist"
echo "=== plist line count ==="
wc -l "$WORK/plist"

# Build the package (FreeBSD 14 default = .pkg / zstd).
pkg create -M "$WORK/manifest.json" -p "$WORK/plist" -r "$STAGE" -o "$DIST"

echo "Built:"
ls -la "$DIST"/il-postino-*.pkg
