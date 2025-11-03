#!/usr/bin/env bash
set -euo pipefail

# Compose project (defaults to folder name), per docs you can override with -p/COMPOSE_PROJECT_NAME.
# https://docs.docker.com/compose/how-tos/project-name/
PROJ="${COMPOSE_PROJECT_NAME:-$(basename vendor/opentelemetry-demo)}"

# Count running containers per Compose service using labels; no jq/JSON needed.
# Uses Go-template --format and label filter. Docs: formatting & filters.
# https://docs.docker.com/engine/cli/formatting/  https://docs.docker.com/engine/cli/filter/
docker ps --filter "label=com.docker.compose.project=${PROJ}" \
  --format '{{.Label "com.docker.compose.service"}}' \
| awk '
function norm(s){ gsub(/_/,"-",s); s=tolower(s); sub(/-service$/,"",s); sub(/service$/,"",s); return s }
NF { c[norm($1)]++ }
END{
  printf("{"); first=1;
  for (k in c){ if(!first) printf(","); printf("\"%s\":%d", k, c[k]); first=0 }
  print "}"
}'
