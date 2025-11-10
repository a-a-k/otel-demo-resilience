#!/usr/bin/env bash
set -euo pipefail
P_FAIL="${1:?fraction like 0.3}"
ALLOWLIST="${2:?services_allowlist.txt}"
WINDOW="${3:-30}"
LOG_FILE="${4:-window_log.jsonl}"
PROJ="${COMPOSE_PROJECT_NAME:-$(basename vendor/opentelemetry-demo)}"
COMPOSE_DIR="${COMPOSE_DIR:-vendor/opentelemetry-demo}"

INFRA_RE='(frontend|frontend-proxy|jaeger|grafana|otel-collector|loadgenerator|prometheus|kafka|zipkin)$'

# Build candidate container names from allowlist via Compose labels
CANDIDATES=()
compose_rows=()
if [ -d "$COMPOSE_DIR" ]; then
  while IFS= read -r line; do compose_rows+=("$line"); done < <(
    (cd "$COMPOSE_DIR" && docker compose ps --format '{{.Name}} {{.Service}}' || true)
  )
fi
if [ ${#compose_rows[@]} -eq 0 ]; then
  while IFS= read -r line; do compose_rows+=("$line"); done < <(
    docker ps --filter "label=com.docker.compose.project=${PROJ}" \
      --format '{{.Names}} {{.Label "com.docker.compose.service"}}'
  )
fi
if [ -f "$ALLOWLIST" ] && [ ${#compose_rows[@]} -gt 0 ]; then
  while IFS= read -r svc; do
    [[ -z "$svc" || "$svc" =~ ^# ]] && continue
    svc_norm=$(echo "$svc" | tr '[:upper:]' '[:lower:]' | sed -E 's/_/-/g; s/(-)?service$//')
    for row in "${compose_rows[@]}"; do
      name=$(echo "$row" | awk '{print $1}')
      lab=$(echo "$row" | awk '{print $2}')
      lab_norm=$(echo "$lab" | tr '[:upper:]' '[:lower:]' | sed -E 's/_/-/g; s/(-)?service$//')
      if [[ "$lab_norm" == "$svc_norm" && -n "$name" ]]; then
        CANDIDATES+=("$name")
      fi
    done
  done < "$ALLOWLIST"
fi

# Fallback auto-discovery excluding infra/entrypoints
if [ ${#CANDIDATES[@]} -eq 0 ]; then
  while IFS= read -r name; do CANDIDATES+=("$name"); done < <(
    for row in "${compose_rows[@]}"; do echo "$row"; done \
    | awk 'NF==2 {print $1" "$2}' \
    | awk "!/$INFRA_RE/ {print \$1}"
  )
fi

TOTAL=${#CANDIDATES[@]}
export TOTAL

: > /tmp/killset.txt
if [ "$TOTAL" -gt 0 ] && awk "BEGIN {exit !($P_FAIL > 0)}"; then
  python3 - "$P_FAIL" "${CANDIDATES[@]}" > /tmp/killset.txt <<'PY'
import sys, random, math
p = float(sys.argv[1])
candidates = [c for c in sys.argv[2:] if c]
n = len(candidates)
kill_n = 0
if n > 0 and p > 0:
    kill_n = int(round(n * p))
    if kill_n == 0:
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
  mapfile -t VICTIMS < /tmp/killset.txt
  echo "[chaos] killset (p=$P_FAIL, total=$TOTAL):"
  printf '  %s\n' "${VICTIMS[@]}"
  docker update --restart=no "${VICTIMS[@]}" >/dev/null 2>&1 || true
  xargs -r -a /tmp/killset.txt -n1 -P4 docker stop --time 1 || true
  sleep "${WINDOW}"
  xargs -r -a /tmp/killset.txt -n1 -P4 docker start || true
  docker update --restart=unless-stopped "${VICTIMS[@]}" >/dev/null 2>&1 || true
else
  echo "[chaos] killset empty (total=$TOTAL, p=$P_FAIL)"
  sleep "${WINDOW}"
fi
