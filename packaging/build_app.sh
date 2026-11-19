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
npm --prefix web ci --silent
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
# The usage-stats collector likewise: FLACKEY_BUILD_TELEMETRY_URL (https only) and FLACKEY_BUILD_TELEMETRY_KEY,
# set only by the release workflow from repository secrets. Unset (dev builds, the demo, a plain push), the
# copy has no collector address and sends nothing. Values are never printed.
BUILD_JSON="src/flackey/assets/build.json"
rm -f "$BUILD_JSON"  # a leftover from an earlier keyed build must never ship in a keyless one
python3 - "$BUILD_JSON" <<'PY'
import json, os, sys
out = {}
api_id, api_hash = os.environ.get("FLACKEY_TELEGRAM_API_ID", ""), os.environ.get("FLACKEY_TELEGRAM_API_HASH", "")
if api_id and api_hash:
    out.update(telegram_api_id=int(api_id), telegram_api_hash=api_hash)
url, key = os.environ.get("FLACKEY_BUILD_TELEMETRY_URL", ""), os.environ.get("FLACKEY_BUILD_TELEMETRY_KEY", "")
if url or key:
    if not (url.startswith("https://") and key):
        sys.exit("FLACKEY_BUILD_TELEMETRY_URL must be https:// and come with FLACKEY_BUILD_TELEMETRY_KEY")
    out.update(telemetry_url=url, telemetry_key=key)
if out:
    with open(sys.argv[1], "w", encoding="utf-8") as f:
        json.dump(out, f)
        f.write("\n")
print("    Telegram keys:", "baked in" if "telegram_api_id" in out else "none (setup will ask)")
print("    Usage stats collector:", "baked in" if "telemetry_url" in out else "none (sends nothing)")
PY

echo "==> helpers"
# ffmpeg, ffprobe and fpcalc ride inside the app so nobody has to install Homebrew first.
packaging/fetch_helpers.sh

echo "==> bundle"
# `.noindex` keeps Spotlight from listing this build next to the installed app: typing "Flackey" once
# opened a weeks-old local build, which could not update in place and fell back to the installer.
uv run --frozen --group bundle pyinstaller --noconfirm --clean \
  --distpath "$BUILD/dist.noindex" --workpath "$BUILD/work" \
  packaging/Flackey.spec

echo "==> update helper"
# After PyInstaller and *before* codesign: adding a file to a signed bundle breaks the seal, and macOS
# treats a bundle whose signature no longer matches worse than an unsigned one.
#
# Not routed through Flackey.spec's `binaries` list: PyInstaller rewrites the load commands of
# everything in there, and this links nothing but libSystem.
HELPER_DIR="$BUILD/dist.noindex/Flackey.app/Contents/Frameworks/bin"
mkdir -p "$HELPER_DIR"
# Pinned to the LSMinimumSystemVersion in Flackey.spec: left unset, clang targets the build machine's
# SDK, and a helper that needs a newer macOS than the app breaks Update for everyone on an older one.
clang -O2 -Wall -Wextra -Werror -mmacosx-version-min=12.0 -o "$HELPER_DIR/flackey-update-helper" packaging/update_helper.c
chmod 755 "$HELPER_DIR/flackey-update-helper"

# A bundle assembled file by file carries no signature, and macOS treats an unsigned-but-modified bundle
# worse than a plainly unsigned one: an ad-hoc signature is free and makes it merely unsigned.
echo "==> ad-hoc signature"
codesign --force --deep --sign - "$BUILD/dist.noindex/Flackey.app"

echo "==> update archive"
# `ditto` rather than `zip`: the bundle contains symlinks (Python.framework) and the signature written
# above, and `zip` flattens the first and drops the second. --keepParent so the archive contains
# `Flackey.app` rather than its contents loose.
#
# After the signature, so the bytes published are the bytes that get signed with the Ed25519 key.
VERSION="$(sed -nE 's/^__version__ = "([^"]+)"/\1/p' src/flackey/__init__.py)"
rm -f "$BUILD/Flackey-$VERSION.zip"
ditto -c -k --keepParent "$BUILD/dist.noindex/Flackey.app" "$BUILD/Flackey-$VERSION.zip"
echo "    $BUILD/Flackey-$VERSION.zip"

echo
echo "Built $BUILD/dist.noindex/Flackey.app"
echo "To build the installer: packaging/build_installer.sh"
