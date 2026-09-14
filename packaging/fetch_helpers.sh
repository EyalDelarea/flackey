#!/usr/bin/env bash
# Fetch the helper programs a packaged Flackey carries inside itself: ffmpeg and ffprobe (static arm64
# builds from eugeneware/ffmpeg-static b6.1.1, FFmpeg 6.0) and fpcalc (acoustid/chromaprint v1.6.1).
# Each download is pinned by SHA-256 and checked before it is unpacked. Result:
#   packaging/build/bin/{ffmpeg,ffprobe,fpcalc}   executables the .app bundles at Contents/Frameworks/bin
#   packaging/build/bin/licenses/                 the licence texts that travel with them
# Downloads are cached in packaging/build/helpers so a rebuild does not fetch 40 MB again.
# Runs on macOS's own /bin/bash (3.2): no associative arrays, no mapfile.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE="$ROOT/packaging/build/helpers"
BIN="$ROOT/packaging/build/bin"
FFMPEG_STATIC="https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1"
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

fetch ffmpeg-darwin-arm64.gz  "$FFMPEG_STATIC/ffmpeg-darwin-arm64.gz"  8923876afa8db5585022d7860ec7e589af192f441c56793971276d450ed3bbfa
fetch ffprobe-darwin-arm64.gz "$FFMPEG_STATIC/ffprobe-darwin-arm64.gz" d986a8ec7b030899fe66a8a288ed809a3543338705a3ce178cfb85869c5d80be
fetch chromaprint-fpcalc-1.6.1-macos-arm64.tar.gz "$CHROMAPRINT/chromaprint-fpcalc-1.6.1-macos-arm64.tar.gz" 254f23cb2d290069ba1d3d28199414fbf66d2054fc2f6821c2fc62ed39470a95
if [[ ! -f "$CACHE/ffmpeg.LICENSE" ]]; then
  curl -fsSL --retry 3 -o "$CACHE/ffmpeg.LICENSE" "$FFMPEG_STATIC/darwin-arm64.LICENSE"
fi

gunzip -c "$CACHE/ffmpeg-darwin-arm64.gz"  > "$BIN/ffmpeg"
gunzip -c "$CACHE/ffprobe-darwin-arm64.gz" > "$BIN/ffprobe"
tar -xzf "$CACHE/chromaprint-fpcalc-1.6.1-macos-arm64.tar.gz" -C "$CACHE"
cp "$CACHE/chromaprint-fpcalc-1.6.1-macos-arm64/fpcalc" "$BIN/fpcalc"
cp "$CACHE/ffmpeg.LICENSE" "$BIN/licenses/ffmpeg.LICENSE"
cat > "$BIN/licenses/README.txt" <<'EOF'
ffmpeg and ffprobe: FFmpeg 6.0 static builds from https://github.com/eugeneware/ffmpeg-static (b6.1.1),
licensed as described in ffmpeg.LICENSE (GPL v2 or later for these builds).
fpcalc: Chromaprint 1.6.1 from https://github.com/acoustid/chromaprint, LGPL v2.1 or later.
EOF
chmod +x "$BIN/ffmpeg" "$BIN/ffprobe" "$BIN/fpcalc"

# Each one has to run on this machine, or the bundle would carry three files nobody can execute.
for tool in ffmpeg ffprobe fpcalc; do
  "$BIN/$tool" -version 2>&1 | head -n 1 | sed 's/^/    /'
done
