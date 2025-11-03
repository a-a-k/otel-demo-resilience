#!/usr/bin/env bash
set -euo pipefail
REF="${1:-main}"
mkdir -p vendor
if [ ! -d vendor/opentelemetry-demo ]; then
  git clone --depth=1 --branch "$REF" https://github.com/open-telemetry/opentelemetry-demo.git vendor/opentelemetry-demo
else
  (cd vendor/opentelemetry-demo && git fetch --depth=1 origin "$REF" && git checkout -f "$REF")
fi
