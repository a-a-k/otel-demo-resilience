#!/usr/bin/env bash
set -euo pipefail

# Determine Compose project name (defaults to folder name)
PROJ="${COMPOSE_PROJECT_NAME:-$(basename vendor/opentelemetry-demo)}"

# Count running containers per Compose service using labels
# Docs: Docker object labels; docker CLI Go-templates. :contentReference[oaicite:0]{index=0}
docker ps --filter "label=com.docker.compose.project=${PROJ}" \
  --format '{{.Label "com.docker.compose.service"}}' \
| awk '
function norm(s){ gsub(/_/,"-",s); s=tolower(s); sub(/-service$/,"",s); sub(/service$/,"",s); return s }
NF { c[norm($1)]++ }
END{
  printf("{"); first=1;
  for (k in c){
    if(!first) printf(",");
    printf("\"%s\":%d", k, c[k]);
    first=0
  }
  print "}"
}'
