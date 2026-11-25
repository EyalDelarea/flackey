#!/usr/bin/env bash
# build_openssl.sh PREFIX -- a static OpenSSL for the Intel Mac build's cryptography.
#
# cryptography publishes no x86_64 macOS wheel, so on an Intel runner uv builds it from source. Linked
# against a shared libssl.3.dylib (Homebrew's), PyInstaller puts that name next to the Python runtime's own
# older libssl.3.dylib, the older one wins, and the app dies at import with "Symbol not found:
# _SSL_get0_group_name". Linking OpenSSL statically removes the dylib from the question. Built here rather
# than taken from Homebrew because Homebrew's targets the runner's macOS, and the app must run on 12.
#
# The tarball is pinned by SHA-256. The result is cached by the workflow; a rebuild takes a few minutes.
set -euo pipefail

PREFIX="${1:?usage: build_openssl.sh PREFIX}"
VERSION=3.5.9
SHA256=603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a
MACOS_MIN=12.0

if [[ -f "$PREFIX/lib/libssl.a" && -f "$PREFIX/.built-$VERSION" ]]; then
  echo "    OpenSSL $VERSION already at $PREFIX"
  exit 0
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
curl -fsSL --retry 3 -o "$work/openssl.tar.gz" \
  "https://github.com/openssl/openssl/releases/download/openssl-$VERSION/openssl-$VERSION.tar.gz"
echo "$SHA256  $work/openssl.tar.gz" | shasum -a 256 -c - >/dev/null
tar -xzf "$work/openssl.tar.gz" -C "$work"
cd "$work/openssl-$VERSION"

case "$(uname -m)" in
  x86_64) TARGET=darwin64-x86_64-cc ;;
  arm64)  TARGET=darwin64-arm64-cc ;;
  *) echo "no OpenSSL target for $(uname -m)" >&2; exit 1 ;;
esac
./Configure "$TARGET" no-shared no-tests no-docs --prefix="$PREFIX" --libdir=lib \
  "-mmacosx-version-min=$MACOS_MIN" >/dev/null
make -j"$(sysctl -n hw.ncpu)" >/dev/null
make install_sw >/dev/null
touch "$PREFIX/.built-$VERSION"
echo "    OpenSSL $VERSION (static, macOS $MACOS_MIN+) at $PREFIX"
