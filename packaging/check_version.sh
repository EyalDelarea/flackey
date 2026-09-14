#!/usr/bin/env bash
# check_version.sh vX.Y.Z -- refuse a release tag that does not match the version in the tree.
# Both hand-written copies are compared: pyproject.toml and src/flackey/__init__.py.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tag="${1:-}"
if [[ ! "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "usage: check_version.sh vX.Y.Z (got '${tag}')" >&2
  exit 1
fi

want="${tag#v}"
pyproject="$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$ROOT/pyproject.toml" | head -n 1)"
package="$(sed -n 's/^__version__ = "\([^"]*\)"/\1/p' "$ROOT/src/flackey/__init__.py" | head -n 1)"
if [[ "$pyproject" != "$want" || "$package" != "$want" ]]; then
  echo "tag $tag does not match the tree: pyproject.toml says '$pyproject', flackey/__init__.py says '$package'" >&2
  exit 1
fi

echo "version $want: tag, pyproject.toml and flackey/__init__.py agree"
