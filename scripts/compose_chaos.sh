#!/usr/bin/env bash
set -euo pipefail
P_FAIL="${1:?fraction like 0.3}"
ALLOWLIST="${2:?services_allowlist.txt}"
WINDOW="${3:-30}"
PROJ="${COMPOSE_PROJECT_NAME:-$(basename vendor/opentelemetry-demo)}"

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

# draw K ~ Binomial(TOTAL, p_fail); allow K=0 to mirror independent sampling
KILL_N=$(python3 - <<'PY'
import random
n=int("$TOTAL")
p=float("$P_FAIL")
print(sum(1 for _ in range(n) if random.random()<p))
PY
)

if [ "$KILL_N" -le 0 ]; then
  echo "No kills this window (K=0 of $TOTAL)"; sleep "$WINDOW"; exit 0
fi

shuf -e "${CANDIDATES[@]}" | head -n "${KILL_N}" > /tmp/killset.txt
echo "Stopping $(wc -l </tmp/killset.txt) of $TOTAL containers for ${WINDOW}s"; cat /tmp/killset.txt

# stop, wait window, then start back
xargs -r -a /tmp/killset.txt -n1 -P4 docker stop --time 1 || true
sleep "${WINDOW}"
xargs -r -a /tmp/killset.txt -n1 -P4 docker start || true
