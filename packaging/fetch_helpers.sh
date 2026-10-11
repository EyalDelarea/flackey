#!/usr/bin/env bash
# Fetch the helper programs a packaged Flackey carries inside itself: ffmpeg and ffprobe (built from
# source by build_ffmpeg.sh, slimmed to what Flackey uses) and fpcalc (acoustid/chromaprint v1.6.1).
# Each download is pinned by SHA-256 and checked before it is unpacked. Result:
#   packaging/build/bin/{ffmpeg,ffprobe,fpcalc}   executables the .app bundles at Contents/Frameworks/bin
#   packaging/build/bin/licenses/                 the licence texts that travel with them
# Downloads and the ffmpeg build are cached in packaging/build/helpers so a rebuild repeats neither.
# Runs on macOS's own /bin/bash (3.2): no associative arrays, no mapfile.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE="$ROOT/packaging/build/helpers"
BIN="$ROOT/packaging/build/bin"
CHROMAPRINT="https://github.com/acoustid/chromaprint/releases/download/v1.6.1"

mkdir -p "$CACHE" "$BIN/licenses"

# fetch NAME URL SHA256 -- download into the cache unless already there, then verify. A file that fails
# the check is deleted so the next run fetches it again instead of trusting a partial download.
fetch() {
  local name="$1" url="$2" sha="$3" file="$CACHE/$1"
  if [[ ! -f "$file" ]]; then
    echo "    fetching $name"
    curl -fsSL --retry 3 -o "$file.part" "$url"
    mv "$file.part" "$file"
  fi
  if ! echo "$sha  $file" | shasum -a 256 -c - >/dev/null 2>&1; then
    rm -f "$file"
    echo "checksum mismatch for $name (deleted; run again)" >&2
    exit 1
  fi
}

fetch chromaprint-fpcalc-1.6.1-macos-arm64.tar.gz "$CHROMAPRINT/chromaprint-fpcalc-1.6.1-macos-arm64.tar.gz" 254f23cb2d290069ba1d3d28199414fbf66d2054fc2f6821c2fc62ed39470a95

"$ROOT/packaging/build_ffmpeg.sh" "$BIN"
tar -xzf "$CACHE/chromaprint-fpcalc-1.6.1-macos-arm64.tar.gz" -C "$CACHE"
cp "$CACHE/chromaprint-fpcalc-1.6.1-macos-arm64/fpcalc" "$BIN/fpcalc"
cat > "$BIN/licenses/README.txt" <<'EOF'
ffmpeg and ffprobe: FFmpeg built from its unmodified release source, LGPL v2.1 or later. ffmpeg.README
names the release, where its source is and how it was configured; ffmpeg.LICENSE is the licence.
fpcalc: Chromaprint 1.6.1 from https://github.com/acoustid/chromaprint, LGPL v2.1 or later.
EOF
chmod +x "$BIN/fpcalc"

# Each one has to run on this machine, or the bundle would carry three files nobody can execute.
for tool in ffmpeg ffprobe fpcalc; do
  "$BIN/$tool" -version 2>&1 | head -n 1 | sed 's/^/    /'
done
