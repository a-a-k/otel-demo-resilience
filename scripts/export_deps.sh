#!/usr/bin/env bash
set -euo pipefail
LOOKBACK_MIN="${1:-30}"
END_TS_MS=$(($(date +%s%N)/1000000))
LOOKBACK_MS=$((LOOKBACK_MIN*60*1000))
# Jaeger API is proxied at /jaeger/api behind the frontend-proxy (port 8080 by default)
curl -fsS "http://localhost:8080/jaeger/api/dependencies?endTs=${END_TS_MS}&lookback=${LOOKBACK_MS}"
