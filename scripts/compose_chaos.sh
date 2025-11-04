#!/usr/bin/env bash
set -euo pipefail
P_FAIL="${1:?fraction like 0.3}"
ALLOWLIST="${2:?services_allowlist.txt}"
WINDOW="${3:-30}"
PROJ="${COMPOSE_PROJECT_NAME:-$(basename vendor/opentelemetry-demo)}"

# enumerate containers for allowed services (prefer allowlist)

# fallback: auto-discover app services by project label, excluding infra/entrypoints
if [ "${#CANDIDATES[@]}" -eq 0 ]; then
  mapfile -t CANDIDATES < <(
    docker ps --filter "label=com.docker.compose.project=${PROJ}" \
      --format '{{.Names}} {{.Label "com.docker.compose.service"}}' \
    | awk 'NR>0 && NF==2 {print $1" " $2}' \
    | awk '!/ (frontend|frontend-proxy|jaeger|grafana|otel-collector|loadgenerator|prometheus|kafka|zipkin)$/{print $1}'
  )
fi

TOTAL=${#CANDIDATES[@]}
if [ "$TOTAL" -eq 0 ]; then
  echo "no eligible containers"; sleep "$WINDOW"; exit 0
fi

# draw K ~ Binomial(TOTAL, p_fail); allow K=0 to mirror independent sampling
KILL_N=$(python3 - <<'PY'
import random, os
n=int(os.environ.get("TOTAL","0"))
p=float(os.environ.get("P_FAIL","0"))
print(sum(1 for _ in range(n) if random.random()<p))
PY
)
export TOTAL P_FAIL

if [ "$KILL_N" -le 0 ]; then
  echo "No kills this window (K=0 of $TOTAL)"; sleep "$WINDOW"; exit 0
fi

if [ "$KILL_N" -le 0 ]; then
  echo "No kills this window (K=0 of $TOTAL)"; sleep "${WINDOW}"; exit 0
fi

shuf -e "${CANDIDATES[@]}" | head -n "${KILL_N}" > /tmp/killset.txt
echo "Stopping $(wc -l </tmp/killset.txt) of $TOTAL containers for ${WINDOW}s"; cat /tmp/killset.txt

# log killed service names ➜ window_log.jsonl
python3 - <<'PY' "$P_FAIL" "$WINDOW" >> window_log.jsonl
import sys,subprocess,json
p=float(sys.argv[1]); win=int(sys.argv[2])
with open("/tmp/killset.txt") as f: names=[l.strip() for l in f if l.strip()]
svcs=set()
for n in names:
    try:
        s=subprocess.check_output(
          ["docker","inspect","-f","{{ index .Config.Labels \"com.docker.compose.service\"}}", n],
          text=True).strip()
        if s: svcs.add(s)
    except Exception:
        pass
print(json.dumps({"p_fail":p,"killed":len(names),"services":sorted(svcs),"window_s":win}))
PY

# stop, wait window, then start back
xargs -r -a /tmp/killset.txt -n1 -P4 docker stop --time 1 || true
sleep "${WINDOW}"
xargs -r -a /tmp/killset.txt -n1 -P4 docker start || true
