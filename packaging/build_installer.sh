#!/usr/bin/env bash
# Build the macOS installer package from packaging/build/dist/Flackey.app.
# Run packaging/build_app.sh first, or pass the app path as the first argument.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="${1:-$ROOT/packaging/build/dist/Flackey.app}"
OUT="$ROOT/packaging/build/Flackey.pkg"
PKGROOT="$(mktemp -d "$ROOT/packaging/build/pkgroot.XXXXXX")"
trap 'rm -rf "$PKGROOT"' EXIT

if [[ ! -d "$APP" ]]; then
  echo "missing app bundle: $APP" >&2
  echo "run packaging/build_app.sh first" >&2
  exit 1
fi

VERSION="$(
  sed -n 's/^__version__ = "\([^"]*\)"/\1/p' "$ROOT/src/flackey/__init__.py" | head -n 1
)"
if [[ -z "$VERSION" ]]; then
  echo "could not read Flackey version" >&2
  exit 1
fi

rm -f "$OUT"
mkdir -p "$PKGROOT/Applications"
# Stage a clean copy so Finder metadata/resource forks do not become AppleDouble files in the package.
ditto --norsrc --noextattr "$APP" "$PKGROOT/Applications/Flackey.app"
find "$PKGROOT" -name '._*' -type f -delete
xattr -cr "$PKGROOT"
pkgbuild \
  --root "$PKGROOT" \
  --install-location / \
  --identifier com.flackey.app \
  --version "$VERSION" \
  "$OUT"

echo "Built $OUT"
