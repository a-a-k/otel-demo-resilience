#!/usr/bin/env bash
set -euo pipefail
P_FAIL="${1:?fraction like 0.3}"
DISALLOWLIST="${2:-config/services_disallowlist.txt}"
WINDOW="${3:-30}"
LOG_FILE="${4:-window_log.jsonl}"
PROJ="${COMPOSE_PROJECT_NAME:-$(basename vendor/opentelemetry-demo)}"
COMPOSE_DIR="${COMPOSE_DIR:-vendor/opentelemetry-demo}"

norm() {
  echo "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/_/-/g; s/(-)?service$//; s/-detection$//'
}

# Load disallowlist (normalized)
declare -A DISALLOW
if [ -f "$DISALLOWLIST" ]; then
  while IFS= read -r svc; do
    [[ -z "$svc" || "$svc" =~ ^# ]] && continue
    key=$(norm "$svc")
    DISALLOW["$key"]=1
  done < "$DISALLOWLIST"
fi

# Build candidate container names from compose services, excluding disallowlist
CANDIDATES=()
compose_rows=()
if [ -d "$COMPOSE_DIR" ]; then
  while IFS= read -r line; do compose_rows+=("$line"); done < <(
    (cd "$COMPOSE_DIR" && docker compose ps --all --format '{{.Name}} {{.Service}}' || true)
  )
fi
if [ ${#compose_rows[@]} -eq 0 ]; then
  while IFS= read -r line; do compose_rows+=("$line"); done < <(
    docker ps --all --filter "label=com.docker.compose.project=${PROJ}" \
      --format '{{.Names}} {{.Label "com.docker.compose.service"}}'
  )
fi
if [ ${#compose_rows[@]} -gt 0 ]; then
  for row in "${compose_rows[@]}"; do
    name=$(echo "$row" | awk '{print $1}')
    lab=$(echo "$row" | awk '{print $2}')
    lab_norm=$(norm "$lab")
    if [[ -n "$name" && -z "${DISALLOW[$lab_norm]:-}" ]]; then
      CANDIDATES+=("$name")
    fi
  done
fi

# Fallback auto-discovery (still honor disallowlist)
if [ ${#CANDIDATES[@]} -eq 0 ]; then
  while IFS= read -r row; do
    name=$(echo "$row" | awk '{print $1}')
    lab=$(echo "$row" | awk '{print $2}')
    lab_norm=$(norm "$lab")
    if [[ -n "$name" && -z "${DISALLOW[$lab_norm]:-}" ]]; then
      CANDIDATES+=("$name")
    fi
  done < <(docker ps --filter "label=com.docker.compose.project=${PROJ}" \
      --format '{{.Names}} {{.Label "com.docker.compose.service"}}')
fi

TOTAL=${#CANDIDATES[@]}
export TOTAL

# Ensure all eligible containers are up before sampling a kill set.
if [ ${#CANDIDATES[@]} -gt 0 ]; then
  echo "[chaos] ensuring eligible containers are running..."
  printf '%s\n' "${CANDIDATES[@]}" | xargs -r -n10 docker start >/dev/null 2>&1 || true
fi

: > /tmp/killset.txt
if [ "$TOTAL" > 0 ] && awk "BEGIN {exit !($P_FAIL > 0)}"; then
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
  echo "[chaos] stopping victims (timeout=1s)..."
  set -x
  xargs -r -a /tmp/killset.txt -n1 -P4 docker stop --timeout 1 || true
  set +x
  echo "[chaos] stop issued; victim statuses:"
  ANOMALIES=()
  for v in "${VICTIMS[@]}"; do
    status=$(docker inspect -f '{{.State.Status}}' "$v" 2>/dev/null || echo "unknown")
    echo "  $v -> $status"
    if [[ "$status" != "exited" && "$status" != "dead" ]]; then
      ANOMALIES+=("$v:$status")
    fi
  done
  if [ ${#ANOMALIES[@]} -gt 0 ]; then
    echo "[chaos] warning: anomalies after stop: ${ANOMALIES[*]}" >&2
    python3 - <<'PY' "$P_FAIL" "$WINDOW" "$LOG_FILE" "${ANOMALIES[@]}"
import sys, json, os
p=float(sys.argv[1]); win=int(sys.argv[2]); log=sys.argv[3]
anoms=sys.argv[4:]
names=[]
try:
    with open('/tmp/killset.txt') as f:
        names=[l.strip() for l in f if l.strip()]
except Exception:
    names=[]
entry={
    'p_fail': p,
    'eligible': int(os.environ.get('TOTAL','0')),
    'killed': len(names),
    'services': sorted(names),
    'window_s': win,
    'anomaly': {'bad_stop': anoms}
}
with open(log, 'a') as fh:
    fh.write(json.dumps(entry) + "\n")
PY
  fi
  echo "[chaos] sleeping for ${WINDOW}s..."
  sleep "${WINDOW}"
  echo "[chaos] waking up; starting victims..."
  set -x
  xargs -r -a /tmp/killset.txt -n1 -P4 docker start || true
  set +x
  echo "[chaos] waking up; starting victims..."
  echo "[chaos] start issued; victim statuses:"
  for v in "${VICTIMS[@]}"; do
    status=$(docker inspect -f '{{.State.Status}}' "$v" 2>/dev/null || echo "unknown")
    echo "  $v -> $status"
  done
else
  echo "[chaos] killset empty (total=$TOTAL, p=$P_FAIL)"
  echo "[chaos] sleeping for ${WINDOW}s (no victims)..."
  sleep "${WINDOW}"
  echo "[chaos] sleep done (no victims)"
fi
