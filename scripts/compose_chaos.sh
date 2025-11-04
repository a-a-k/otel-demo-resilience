#!/usr/bin/env bash
set -euo pipefail
P_FAIL="${1:?fraction like 0.3}"
ALLOWLIST="${2:?services_allowlist.txt}"
WINDOW="${3:-30}"
PROJ="${COMPOSE_PROJECT_NAME:-$(basename vendor/opentelemetry-demo)}"

INFRA_RE='(frontend|frontend-proxy|jaeger|grafana|otel-collector|loadgenerator|prometheus|kafka|zipkin)$'

# Build candidate container names from allowlist via Compose labels
CANDIDATES=()
if [ -f "$ALLOWLIST" ]; then
  while IFS= read -r svc; do
    [[ -z "$svc" || "$svc" =~ ^# ]] && continue
    # normalize (lower, dashes)
    svc_norm=$(echo "$svc" | tr '[:upper:]' '[:lower:]' | sed -E 's/_/-/g; s/(-)?service$//')
    while IFS= read -r row; do
      name=$(echo "$row" | awk '{print $1}')
      lab=$(echo "$row" | awk '{print $2}')
      lab_norm=$(echo "$lab" | tr '[:upper:]' '[:lower:]' | sed -E 's/_/-/g; s/(-)?service$//')
      if [[ "$lab_norm" == "$svc_norm" ]]; then CANDIDATES+=("$name"); fi
    done < <(
      docker ps --filter "label=com.docker.compose.project=${PROJ}" \
        --format '{{.Names}} {{.Label "com.docker.compose.service"}}'
    )
  done < "$ALLOWLIST"
fi

# Fallback auto-discovery excluding infra/entrypoints
if [ ${#CANDIDATES[@]} -eq 0 ]; then
  while IFS= read -r name; do CANDIDATES+=("$name"); done < <(
    docker ps --filter "label=com.docker.compose.project=${PROJ}" \
      --format '{{.Names}} {{.Label "com.docker.compose.service"}}' \
    | awk 'NF==2 {print $1" "$2}' \
    | awk "!/$INFRA_RE/ {print \$1}"
  )
fi

TOTAL=${#CANDIDATES[@]}

# Draw K ~ Binomial(TOTAL, p_fail) (allow K=0)
export TOTAL P_FAIL
KILL_N=$(python3 - <<'PY'
import os, random
n=int(os.environ.get('TOTAL','0'))
p=float(os.environ.get('P_FAIL','0'))
print(sum(1 for _ in range(n) if random.random() < p))
PY
)

# Choose kill set (possibly empty)
if [ "$TOTAL" -gt 0 ] && [ "$KILL_N" -gt 0 ]; then
  shuf -e "${CANDIDATES[@]}" | head -n "${KILL_N}" > /tmp/killset.txt
else
  : > /tmp/killset.txt
fi

# Log window summary (always)
python3 - <<'PY' "$P_FAIL" "$WINDOW" >> window_log.jsonl
import sys, subprocess, json, os
p=float(sys.argv[1]); win=int(sys.argv[2])
names=[]
try:
    with open('/tmp/killset.txt') as f:
        names=[l.strip() for l in f if l.strip()]
except Exception:
    names=[]
svcs=set()
for n in names:
    try:
        s=subprocess.check_output([
            'docker','inspect','-f','{{ index .Config.Labels "com.docker.compose.service"}}', n
        ], text=True).strip()
        if s: svcs.add(s)
    except Exception:
        pass
print(json.dumps({
  'p_fail': p,
  'eligible': int(os.environ.get('TOTAL','0')),
  'killed': len(names),
  'services': sorted(svcs),
  'window_s': win
}))
PY

# Execute chaos (graceful stop/start only if K>0)
if [ -s /tmp/killset.txt ]; then
  xargs -r -a /tmp/killset.txt -n1 -P4 docker stop --time 1 || true
  sleep "${WINDOW}"
  xargs -r -a /tmp/killset.txt -n1 -P4 docker start || true
else
  sleep "${WINDOW}"
fi
