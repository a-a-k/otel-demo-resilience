#!/usr/bin/env bash
set -euo pipefail
P_FAIL="${1:?fraction like 0.3}"
ALLOWLIST="${2:?services_allowlist.txt}"
WINDOW="${3:-30}"

# enumerate container names for allowed services
mapfile -t CANDIDATES < <(
  docker ps --format '{{.Names}} {{.Label "com.docker.compose.service"}}' \
  | awk 'NF==2 {print $1" "$2}' \
  | while read name svc; do
      if grep -qx "$svc" "$ALLOWLIST"; then echo "$name"; fi
    done
)

TOTAL=${#CANDIDATES[@]}
if [ "$TOTAL" -eq 0 ]; then
  echo "no eligible containers"; sleep "$WINDOW"; exit 0
fi

# draw a sample to stop
KILL_N=$(python3 - <<PY
import math; print(max(1, math.floor($TOTAL * float("$P_FAIL"))))
PY
)

shuf -e "${CANDIDATES[@]}" | head -n "${KILL_N}" > /tmp/killset.txt
echo "Stopping $(wc -l </tmp/killset.txt) of $TOTAL containers for ${WINDOW}s"; cat /tmp/killset.txt

# stop, wait window, then start back
xargs -r -a /tmp/killset.txt -n1 -P4 docker stop --time 1 || true
sleep "${WINDOW}"
xargs -r -a /tmp/killset.txt -n1 -P4 docker start || true
