#!/usr/bin/env bash
# latest_json.sh vX.Y.Z Flackey.zip Flackey.zip.sha256 -- the small JSON file a release carries so the
# site (and, later, an in-app update check) can read the current version without the GitHub API.
set -euo pipefail

tag="${1:?tag}"
zip="${2:?zip}"
sums="${3:?sha256 file}"
sha="$(awk '{print $1}' "$sums")"
size="$(stat -c %s "$zip" 2>/dev/null || stat -f %z "$zip")"
python3 - "$tag" "$sha" "$size" <<'EOF'
import datetime
import json
import sys

tag, sha, size = sys.argv[1:]
print(json.dumps({
    "version": tag[1:],
    "tag": tag,
    "url": f"https://github.com/EyalDelarea/flackey/releases/download/{tag}/Flackey.zip",
    "sha256": sha,
    "size": int(size),
    "published_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "arch": "arm64",
    "min_macos": "12.0",
}, indent=2))
EOF
