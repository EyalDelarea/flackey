#!/usr/bin/env bash
# Build Flackey.app. Run from the repository root: packaging/build_app.sh
#
# The result is unsigned, so it is for people you can talk to -- see packaging/README-for-friends.md for
# what the person receiving it has to do on first launch.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/packaging/build"
cd "$ROOT"

echo "==> web UI"
npm --prefix web install --silent
npm --prefix web run build

echo "==> icon"
# .icns is the only icon format a bundle takes, and the source asset is a 1024 square PNG.
ICONSET="$BUILD/Flackey.iconset"
rm -rf "$ICONSET" "$BUILD/Flackey.icns"
mkdir -p "$ICONSET"
for size in 16 32 64 128 256 512; do
  sips -z $size $size src/flackey/assets/app-icon.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -z $((size * 2)) $((size * 2)) src/flackey/assets/app-icon.png \
       --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$BUILD/Flackey.icns"

echo "==> build defaults"
# The Telegram keys a packaged copy carries. From CI these come from repository secrets; locally, export
# FLACKEY_TELEGRAM_API_ID and FLACKEY_TELEGRAM_API_HASH before running this script, or leave them unset
# to build a copy whose setup screen asks for keys.
BUILD_JSON="src/flackey/assets/build.json"
if [[ -n "${FLACKEY_TELEGRAM_API_ID:-}" && -n "${FLACKEY_TELEGRAM_API_HASH:-}" ]]; then
  printf '{"telegram_api_id": %s, "telegram_api_hash": "%s"}\n' \
    "$FLACKEY_TELEGRAM_API_ID" "$FLACKEY_TELEGRAM_API_HASH" > "$BUILD_JSON"
  echo "    Telegram keys: baked in"
else
  rm -f "$BUILD_JSON"
  echo "    Telegram keys: none (setup will ask)"
fi

echo "==> helpers"
# ffmpeg, ffprobe and fpcalc ride inside the app so nobody has to install Homebrew first.
packaging/fetch_helpers.sh

echo "==> bundle"
uv run --with pyinstaller pyinstaller --noconfirm --clean \
  --distpath "$BUILD/dist" --workpath "$BUILD/work" \
  packaging/Flackey.spec

echo "==> update helper"
# The program that swaps the new bundle in for the old one after the app has quit. Built here rather
# than fetched: it is 300 lines of C in this repository, and a self-updater that downloads its own
# updater would defeat the point of signing the payload.
#
# Copied in after PyInstaller and *before* codesign, which is not optional. Adding a file to a signed
# bundle breaks the seal, and macOS treats a bundle whose signature no longer matches far worse than an
# unsigned one -- "Flackey is damaged and can't be opened", with no way past it.
#
# Not routed through Flackey.spec's `binaries` list either: PyInstaller rewrites the load commands of
# everything in there, and this program links nothing but libSystem and wants to stay exactly as clang
# emitted it.
HELPER_DIR="$BUILD/dist/Flackey.app/Contents/Frameworks/bin"
mkdir -p "$HELPER_DIR"
clang -O2 -Wall -Wextra -Werror -o "$HELPER_DIR/flackey-update-helper" packaging/update_helper.c
chmod 755 "$HELPER_DIR/flackey-update-helper"

# A bundle assembled file by file carries no signature, and macOS treats an unsigned-but-modified bundle
# worse than a plainly unsigned one: an ad-hoc signature is free and makes it merely unsigned.
echo "==> ad-hoc signature"
codesign --force --deep --sign - "$BUILD/dist/Flackey.app"

echo "==> update archive"
# The seamless update payload: the bundle exactly as it will be installed, and nothing else. `ditto`
# rather than `zip` because the bundle contains symlinks (Python.framework) and the signature written
# above -- `zip` flattens the first and drops the second, and what comes out the other end will not
# launch. --keepParent so the archive contains `Flackey.app` rather than its contents loose.
#
# Made here, after the signature, so the bytes published are the bytes that were signed. The release
# workflow signs this exact file with the Ed25519 key; anything that rewrites it afterwards invalidates
# the signature, which is the point.
VERSION="$(sed -nE 's/^__version__ = "([^"]+)"/\1/p' src/flackey/__init__.py)"
rm -f "$BUILD/Flackey-$VERSION.zip"
ditto -c -k --keepParent "$BUILD/dist/Flackey.app" "$BUILD/Flackey-$VERSION.zip"
echo "    $BUILD/Flackey-$VERSION.zip"

echo
echo "Built $BUILD/dist/Flackey.app"
echo "To build the installer: packaging/build_installer.sh"
