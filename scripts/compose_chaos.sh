#!/usr/bin/env bash
set -euo pipefail
P_FAIL="${1:?fraction like 0.3}"
ALLOWLIST="${2:?services_allowlist.txt}"
WINDOW="${3:-30}"
LOG_FILE="${4:-window_log.jsonl}"
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

: > /tmp/killset.txt
if [ "$TOTAL" -gt 0 ] && awk "BEGIN {exit !($P_FAIL > 0)}"; then
  printf "%s\n" "${CANDIDATES[@]}" | python3 - "$P_FAIL" > /tmp/killset.txt <<'PY'
import sys, random, math
p = float(sys.argv[1])
candidates = [line.strip() for line in sys.stdin if line.strip()]
n = len(candidates)
if n == 0 or p <= 0:
    kill_n = 0
else:
    kill_n = int(round(n * p))
    if p > 0 and kill_n == 0:
        kill_n = 1
    kill_n = min(kill_n, n)
if kill_n > 0:
    random.shuffle(candidates)
    print("\n".join(candidates[:kill_n]))
PY
fi

# Log window summary (always)
python3 - <<'PY' "$P_FAIL" "$WINDOW" "$LOG_FILE"
import sys, subprocess, json, os
p=float(sys.argv[1]); win=int(sys.argv[2])
log=sys.argv[3]
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
with open(log, "a") as fh:
    fh.write(json.dumps({
      'p_fail': p,
      'eligible': int(os.environ.get('TOTAL','0')),
      'killed': len(names),
      'services': sorted(svcs),
      'window_s': win
    }) + "\n")
PY

# Execute chaos (graceful stop/start only if K>0)
if [ -s /tmp/killset.txt ]; then
  xargs -r -a /tmp/killset.txt -n1 -P4 docker stop --time 1 || true
  sleep "${WINDOW}"
  xargs -r -a /tmp/killset.txt -n1 -P4 docker start || true
else
  sleep "${WINDOW}"
fi
