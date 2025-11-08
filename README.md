# otel-resilience-compose

## Quick start (local)
```bash
# optional: run locally (not needed for CI)
bash vendor/fetch-otel-demo.sh
(cd vendor/opentelemetry-demo && docker compose up -d)
bash scripts/wait_http.sh http://localhost:8080/ 120
bash scripts/wait_http.sh http://localhost:8080/jaeger/ui 120
bash scripts/wait_http.sh http://localhost:8080/loadgen/ 120

# export deps and build graph
bash scripts/export_deps.sh 30 > deps.json
python3 scripts/deps_to_graph.py --deps deps.json --entrypoints config/entrypoints.txt --out graph.json

# read replicas & run estimator for p=0.3
bash scripts/read_replicas.sh > replicas.json
python3 scripts/resilience.py --graph graph.json --replicas replicas.json --p 0.3 --samples 120000 --out model.json
```

## Push-and-forget
Commit this repo to GitHub and push. The workflow `.github/workflows/resilience.yml` runs a matrix over:
- `mode ∈ {norepl, repl}` (scales stateless services in `repl`)
- `p_fail ∈ {0.1, 0.3, 0.5, 0.7, 0.9}`

Artifacts per cell:
- `model_<mode>_<p>.json` – Monte‑Carlo estimate.
- `live_<mode>_<p>_N.json` – per-window live measurements from Locust.
- `summary_<mode>_<p>.json` – aggregate (mean/sd).

## Notes
- We **keep the demo’s Locust** load generator. We compute `R_live = 1 - (5xx + transport errors + timeouts)/total` by reading Locust’s stats & exceptions endpoints and counting server-side (5xx) and transport-level errors.  
- Discovery prefers scraping **Jaeger traces** with `scripts/traces_to_deps.py`; if indexing is still empty it automatically falls back to the `/jaeger/api/dependencies` endpoint.
- CI bumps Locust’s default load (`LOCUST_USERS=150`, `LOCUST_SPAWN_RATE=30`) and runs `scripts/validate_chaos_live.py` up front (60 s chaos window with a 15 s delay, retries until ≥80 requests recorded and `R_live ≤ 0.99`) to prove что контейнеры реально глушатся и `R_live` опускается ниже порога; результаты попадают в `validation_*.json`.
- Chaos is implemented as **random container stops** for a fixed window, then automatic restarts, to match a fail‑stop assumption.

See `config/services_allowlist.txt` to decide which app services are eligible for kills; infra (proxy, collector, jaeger, grafana, db/brokers) is excluded by default.
