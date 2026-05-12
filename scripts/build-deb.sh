#!/bin/sh
# scripts/build-deb.sh — build the il-postino .deb in a Debian container.
# Usage: ./scripts/build-deb.sh [bookworm|trixie] [amd64|arm64]
#
# CI's release.yml deb matrix is the canonical build; this script is for
# local iteration only. Produces dist/deb-${DIST}-${ARCH}/il-postino_*.deb.
set -eu

DIST=${1:-trixie}
ARCH=${2:-amd64}
WORK=$(pwd)/dist/deb-${DIST}-${ARCH}
IMAGE="debian:${DIST}-slim"

case "$DIST" in
  bookworm|trixie) ;;
  *) echo "ERROR: unsupported DIST '$DIST' (use bookworm or trixie)" >&2; exit 1 ;;
esac
case "$ARCH" in
  amd64|arm64) ;;
  *) echo "ERROR: unsupported ARCH '$ARCH' (use amd64 or arm64)" >&2; exit 1 ;;
esac

command -v docker >/dev/null 2>&1 || {
  echo "ERROR: docker not found on PATH" >&2
  exit 1
}

mkdir -p dist
rm -rf "$WORK"
mkdir -p "$WORK"

docker run --rm \
  --platform "linux/${ARCH}" \
  -v "$(pwd):/src:ro" \
  -v "$WORK:/build" \
  -w /build \
  "$IMAGE" \
  sh -ec '
    apt-get update
    apt-get install -y --no-install-recommends \
      build-essential debhelper dh-virtualenv \
      python3 python3-dev python3-venv \
      libffi-dev libssl-dev cargo \
      ca-certificates git
    cp -a /src/. .
    dpkg-buildpackage -us -uc -b -a"'"$ARCH"'"
    mv ../*.deb /build/
  '

echo "Built:"
ls -la "$WORK"/*.deb
