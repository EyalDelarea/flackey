#!/usr/bin/env bash
# smoke.sh PATH/TO/Flackey.app -- launch the built app the way Finder would, against a scratch data
# folder, and check that it serves its health endpoint with the helpers found and, when the build was
# given Telegram keys, with Telegram configured. Prints the log tail on failure. Used by CI and by hand.
set -euo pipefail

APP="${1:?usage: smoke.sh Flackey.app}"
PORT="${SMOKE_PORT:-8797}"
S="$(mktemp -d)/flackey-smoke"
mkdir -p "$S/data" "$S/lib"
cleanup() {
  osascript -e 'quit app "Flackey"' >/dev/null 2>&1 || true
  sleep 2
  rm -rf "$S"
}
trap cleanup EXIT

if curl -s -m 1 "http://127.0.0.1:$PORT/api/health" >/dev/null; then
  echo "smoke port $PORT is already in use; set SMOKE_PORT to a free port" >&2
  exit 1
fi

open --env DATA_DIR="$S/data" --env LIBRARY_ROOT="$S/lib" --env WEB_PORT="$PORT" "$APP"
health=""
for _ in $(seq 1 45); do
  health="$(curl -s -m 2 "http://127.0.0.1:$PORT/api/health" || true)"
  [[ -n "$health" ]] && break
  sleep 2
done
if [[ -z "$health" ]]; then
  echo "the app never answered on port $PORT" >&2
  tail -n 40 "$S/data/flackey.log" 2>/dev/null >&2 || true
  exit 1
fi

expect_keys="false"
[[ -n "${FLACKEY_TELEGRAM_API_ID:-}" && -n "${FLACKEY_TELEGRAM_API_HASH:-}" ]] && expect_keys="true"
python3 - "$health" "$expect_keys" <<'EOF'
import json
import sys

h = json.loads(sys.argv[1])
want_keys = sys.argv[2] == "true"
problems = []
if not h.get("ok"):
    problems.append("ok is not true")
if h.get("lossless", {}).get("fpcalc") is not True:
    problems.append("fpcalc not found inside the app")
if h.get("telegram_configured") is not want_keys:
    problems.append(
        f"telegram_configured is {h.get('telegram_configured')}, expected {want_keys}"
    )
if problems:
    print("smoke: " + "; ".join(problems), file=sys.stderr)
    sys.exit(1)
print(
    f"smoke: ok, version {h.get('version')}, "
    f"telegram configured: {h.get('telegram_configured')}, fpcalc: True"
)
EOF
if grep -q -i traceback "$S/data/flackey.log"; then
  echo "smoke: traceback in the log" >&2
  tail -n 40 "$S/data/flackey.log" >&2
  exit 1
fi
