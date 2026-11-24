#!/usr/bin/env bash
# Build the macOS installer package from packaging/build/dist.noindex/Flackey.app.
# Run packaging/build_app.sh first, or pass the app path as the first argument.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="${1:-$ROOT/packaging/build/dist.noindex/Flackey.app}"
OUT="$ROOT/packaging/build/Flackey.pkg"
PKGROOT="$(mktemp -d "$ROOT/packaging/build/pkgroot.XXXXXX")"
# Outside the package root, so it is not packaged itself.
COMPONENTS="$(mktemp "$ROOT/packaging/build/components.XXXXXX")"
trap 'rm -rf "$PKGROOT" "$COMPONENTS"' EXIT

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
# mktemp -d makes the root 0700, and the package records it as the mode of `/`.
chmod 755 "$PKGROOT"

# Not relocatable: by default Installer looks for any Flackey.app macOS knows of with the same bundle
# id (a build folder, a copy in Downloads) and updates that one instead of /Applications/Flackey.app.
not_relocatable() {
  local prefix="$1" i=0
  while /usr/libexec/PlistBuddy -c "Print ${prefix}:${i}" "$COMPONENTS" >/dev/null 2>&1; do
    # Nested bundles (Python.framework) carry no such key of their own.
    if /usr/libexec/PlistBuddy -c "Print ${prefix}:${i}:BundleIsRelocatable" "$COMPONENTS" >/dev/null 2>&1; then
      /usr/libexec/PlistBuddy -c "Set ${prefix}:${i}:BundleIsRelocatable false" "$COMPONENTS"
    fi
    if /usr/libexec/PlistBuddy -c "Print ${prefix}:${i}:ChildBundles" "$COMPONENTS" >/dev/null 2>&1; then
      not_relocatable "${prefix}:${i}:ChildBundles"
    fi
    i=$((i + 1))
  done
}
pkgbuild --analyze --root "$PKGROOT" "$COMPONENTS" >/dev/null
not_relocatable ""
if /usr/libexec/PlistBuddy -c "Print" "$COMPONENTS" | grep -q "BundleIsRelocatable = true"; then
  echo "a bundle in the package is still relocatable" >&2
  exit 1
fi

pkgbuild \
  --root "$PKGROOT" \
  --component-plist "$COMPONENTS" \
  --install-location / \
  --identifier com.flackey.app \
  --version "$VERSION" \
  "$OUT"

echo "Built $OUT"
