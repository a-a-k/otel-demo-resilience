#!/usr/bin/env bash
set -euo pipefail
URL="${1:?url}"; SECS="${2:-120}"
for _ in $(seq 1 "$SECS"); do
  if curl -fsS "$URL" >/dev/null; then exit 0; fi
  sleep 1
done
echo "timeout waiting for $URL" >&2
exit 1
