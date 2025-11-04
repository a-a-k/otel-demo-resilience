#!/usr/bin/env bash
set -euo pipefail

# Determine Compose project name (defaults to folder name)
PROJ="${COMPOSE_PROJECT_NAME:-$(basename vendor/opentelemetry-demo)}"

docker ps --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME:-$(basename vendor/opentelemetry-demo)}" \
  --format '{{.Label "com.docker.compose.service"}}' \
| awk 'NF{gsub(/_/,"-"); s=tolower($1); sub(/-service$/,"",s); sub(/service$/,"",s); c[s]++}
  END{printf("{"); f=1; for(k in c){if(!f)printf(","); printf("\"%s\":%d",k,c[k]); f=0} print "}"}'
