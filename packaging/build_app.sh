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

# A bundle assembled file by file carries no signature, and macOS treats an unsigned-but-modified bundle
# worse than a plainly unsigned one: an ad-hoc signature is free and makes it merely unsigned.
echo "==> ad-hoc signature"
codesign --force --deep --sign - "$BUILD/dist/Flackey.app"

echo
echo "Built $BUILD/dist/Flackey.app"
echo "To build the installer: packaging/build_installer.sh"
