#!/usr/bin/env bash
# Build the slim ffmpeg and ffprobe a packaged Flackey carries (issue #137): FFmpeg 6.1.2 from its
# release tarball, pinned by SHA-256, configured with --disable-everything and then only the pieces
# Flackey's own command lines use. The full static builds this replaces were 43 MB each; most of an
# update download was codecs, filters and network protocols nothing here ever calls.
#
#   packaging/build_ffmpeg.sh OUT_DIR      writes OUT_DIR/ffmpeg and OUT_DIR/ffprobe (.exe on Windows)
#
# Runs on macOS (the arm64 app), Linux (the CI job that runs the audio tests against these exact
# binaries) and Windows under MSYS2's MINGW64 shell (gcc, make, nasm, pkgconf, zlib, diffutils).
# The result is cached in packaging/build/helpers under a key that covers this file, so a rebuild
# that changed nothing here reuses the binaries instead of compiling FFmpeg again.
#
# What has to stay enabled, and why (each list is what the callers in src/flackey actually reach):
#   demuxers  flac/wav/aiff/mp3 are what audiofile.DEMUXERS pins; mov (m4a/aac) and matroska (webm/
#             opus) and ogg are the YouTube reference audio, which is autodetected, not pinned.
#   decoders  everything those containers carry; mp3float is FFmpeg's default mp3 decoder and what the
#             old builds used, so fingerprints and cutoff measurements do not move.
#   encoders  convert.CODECS plus flac, and png for verify's spectrogram picture.
#   muxers    the output formats convert writes, s16le for verify's PCM pipe, image2 for the picture.
#   filters   showspectrumpic, and the ones ffmpeg inserts on its own: format/sample-rate conversion
#             (aformat, aresample, format, scale) and output -t (atrim, trim).
# LGPL: no --enable-gpl, and nothing GPL-only is enabled. --disable-autodetect keeps a build machine's
# own libraries (Homebrew's, MSYS2's) out of the binary; zlib, which the png encoder needs, is the one
# library asked for by name, and it is linked statically everywhere but macOS, where it is the system's.
set -euo pipefail

FFMPEG_VERSION=6.1.2
FFMPEG_SHA256=3b624649725ecdc565c903ca6643d41f33bd49239922e45c9b1442c63dca4e38
MACOS_MIN=12.0   # LSMinimumSystemVersion in Flackey.spec

OUT="${1:?usage: build_ffmpeg.sh OUT_DIR}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE="$ROOT/packaging/build/helpers"

CONFIGURE=(
  --disable-everything --disable-autodetect --disable-network
  --disable-doc --disable-debug --disable-ffplay --disable-avdevice
  --enable-ffmpeg --enable-ffprobe --enable-static --disable-shared
  --enable-zlib
  --enable-protocol=file,pipe
  --enable-demuxer=flac,wav,aiff,mp3,mov,matroska,ogg
  --enable-decoder=flac,mp3float,aac,alac,opus,vorbis,pcm_s16le,pcm_s24le,pcm_s32le,pcm_s16be,pcm_s24be,pcm_s32be,pcm_f32le,pcm_f32be,pcm_u8
  --enable-parser=flac,mpegaudio,aac,opus,vorbis
  --enable-encoder=flac,pcm_s16le,pcm_s24le,pcm_s16be,pcm_s24be,png
  --enable-muxer=flac,wav,aiff,pcm_s16le,image2
  --enable-filter=showspectrumpic,aformat,aresample,anull,atrim,format,scale,null,trim
)

EXE=""
case "$(uname -s)" in
  Darwin)
    PLATFORM=macos
    CONFIGURE+=(--extra-cflags="-mmacosx-version-min=$MACOS_MIN" --extra-ldflags="-mmacosx-version-min=$MACOS_MIN")
    JOBS="$(sysctl -n hw.ncpu)" ;;
  MINGW*|MSYS*)
    PLATFORM=windows
    # -static: no libwinpthread-1.dll or zlib1.dll beside the .exe, which a user's PC would not have.
    CONFIGURE+=(--pkg-config-flags=--static --extra-ldflags=-static)
    EXE=".exe"
    JOBS="$(nproc)" ;;
  *)
    PLATFORM=linux
    CONFIGURE+=(--pkg-config-flags=--static --extra-ldflags=-static)
    JOBS="$(nproc)" ;;
esac

# MSYS2 has no shasum unless perl is installed; Linux and MSYS2 both have sha256sum, macOS only shasum.
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

# The cache key: this script's own bytes (version, digest, configure line) and the machine it targets.
# Not `uname -s` itself, which on Windows carries the OS build number and changes with a runner image.
KEY="$( (cat "${BASH_SOURCE[0]}"; echo "$PLATFORM $(uname -m)") | sha256 | cut -c1-16)"
BUILT="$CACHE/ffmpeg-slim-$KEY"
mkdir -p "$CACHE" "$OUT"

if [[ ! -x "$BUILT/ffmpeg$EXE" || ! -x "$BUILT/ffprobe$EXE" ]]; then
  TARBALL="$CACHE/ffmpeg-$FFMPEG_VERSION.tar.xz"
  if [[ ! -f "$TARBALL" ]]; then
    echo "    fetching ffmpeg-$FFMPEG_VERSION.tar.xz"
    curl -fsSL --retry 3 -o "$TARBALL.part" "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz"
    mv "$TARBALL.part" "$TARBALL"
  fi
  if ! echo "$FFMPEG_SHA256  $TARBALL" | sha256 -c - >/dev/null 2>&1; then
    rm -f "$TARBALL"
    echo "checksum mismatch for ffmpeg-$FFMPEG_VERSION.tar.xz (deleted; run again)" >&2
    exit 1
  fi
  SRC="$CACHE/ffmpeg-$FFMPEG_VERSION-src"
  rm -rf "$SRC" "$BUILT"
  mkdir -p "$SRC"
  tar -xJf "$TARBALL" -C "$SRC" --strip-components=1
  echo "    building ffmpeg $FFMPEG_VERSION (slim, $JOBS jobs)"
  (
    cd "$SRC"
    ./configure --prefix="$BUILT" "${CONFIGURE[@]}" > "$CACHE/ffmpeg-configure.log" 2>&1 \
      || { tail -n 40 ffbuild/config.log >&2; exit 1; }
    make -j"$JOBS" > "$CACHE/ffmpeg-make.log" 2>&1 || { tail -n 40 "$CACHE/ffmpeg-make.log" >&2; exit 1; }
    mkdir -p "$BUILT"
    cp "ffmpeg$EXE" "ffprobe$EXE" COPYING.LGPLv2.1 "$BUILT/"
  )
  strip "$BUILT/ffmpeg$EXE" "$BUILT/ffprobe$EXE"
  printf '%s\n' "${CONFIGURE[@]}" > "$BUILT/configure.txt"
  rm -rf "$SRC"
fi

cp "$BUILT/ffmpeg$EXE" "$BUILT/ffprobe$EXE" "$OUT/"
mkdir -p "$OUT/licenses"
cp "$BUILT/COPYING.LGPLv2.1" "$OUT/licenses/ffmpeg.LICENSE"
{
  echo "ffmpeg and ffprobe: FFmpeg $FFMPEG_VERSION, built for Flackey from the unmodified release source"
  echo "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz (SHA-256 $FFMPEG_SHA256),"
  echo "licensed under the GNU LGPL v2.1 or later (ffmpeg.LICENSE). Configured with:"
  sed 's/^/  /' "$BUILT/configure.txt"
} > "$OUT/licenses/ffmpeg.README"

# Statically linked, or a user's machine is missing a library this build machine happened to have.
case "$PLATFORM" in
  macos)
    if otool -L "$OUT/ffmpeg" "$OUT/ffprobe" | tail -n +2 | grep -vE '^\S|/usr/lib/|/System/Library/' ; then
      echo "ffmpeg links a library outside the system" >&2; exit 1
    fi ;;
  windows)
    if objdump -p "$OUT/ffmpeg.exe" "$OUT/ffprobe.exe" | grep -i 'DLL Name' | grep -viE '(kernel32|user32|advapi32|msvcrt|ws2_32|shell32|ole32|bcrypt|api-ms-win-[a-z0-9-]+|ucrtbase)\.dll' ; then
      echo "ffmpeg.exe needs a DLL Windows does not ship" >&2; exit 1
    fi ;;
esac
