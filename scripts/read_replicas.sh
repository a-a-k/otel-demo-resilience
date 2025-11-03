#!/usr/bin/env bash
set -euo pipefail
# Count running containers per Compose service (requires docker compose v2.20+ for --format json)
docker compose -f vendor/opentelemetry-demo/docker-compose.yml ps --format json \
| jq -r '.[].Service' | sort | uniq -c \
| awk '{printf("{\"service\":\"%s\",\"replicas\":%d}\n",$2,$1)}' \
| jq -s 'reduce .[] as $i ({}; .[$i.service]=$i.replicas)'
