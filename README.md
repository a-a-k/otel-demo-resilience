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
- We keep the demo Locust load so that traffic and telemetry stay warm, but `R_live` is now driven **exclusively by frontend probes** (`cart → checkout`). Each window runs 100 probes; `R_live = probe_ok / probe_total`, while Locust counters are preserved only for debugging. A baseline health check (without chaos) still guards the pipeline before any experiments run.  
- Discovery prefers scraping **Jaeger traces** with `scripts/traces_to_deps.py`; if indexing is empty it automatically falls back to the `/jaeger/api/dependencies` endpoint.
- `scripts/deps_to_graph.py` treats Kafka and other queues as transparent hops: an observed chain `checkout → kafka → fraud-detection` becomes a direct `checkout → fraud` edge so that the Monte Carlo model remains blocking even when the runtime uses async transports.
- CI bumps Locust’s default load (`LOCUST_USERS=150`, `LOCUST_SPAWN_RATE=30`) and runs `scripts/validate_chaos_live.py` up front (60 s chaos window with a 15 s reveal delay, HTTP frontend probes, multiple retries until ≥80 requests **or** a failed probe while `R_live ≤ 0.99`) to prove that chaos actually kills containers; the results land in `validation_*.json`.
- The primary chaos loop uses 60‑second windows, waits 15 s for the failure to surface, and only then captures a 40‑second window via `collect_live.py` so that metrics cover the outage period.
- The GitHub Actions summary enforces monotonicity only for `R_model`; `R_live_mean` is informational and may fluctuate if the load misses a failure window.
- Chaos is implemented as **random container stops** for a fixed window, then automatic restarts, matching a fail‑stop assumption.

See `config/services_allowlist.txt` to decide which app services are eligible for kills; infra (proxy, collector, jaeger, grafana, db/brokers) is excluded by default.
