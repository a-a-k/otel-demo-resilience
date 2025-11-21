# otel-resilience-compose

We study the resilience of the [OpenTelemetry Demo](https://github.com/open-telemetry/opentelemetry-demo) in Docker Compose, derive the dependency graph directly from traces, and compare two semantics for the model: “all edges are blocking” vs “Kafka edges are non-blocking”.

---

## Quick start
```bash
# 1. fetch OpenTelemetry Demo sources (if not yet downloaded)
bash vendor/fetch-otel-demo.sh

# 2. start the demo locally
(cd vendor/opentelemetry-demo && docker compose up -d)
bash scripts/wait_http.sh http://localhost:8080/ 120
bash scripts/wait_http.sh http://localhost:8080/jaeger/ui 120
bash scripts/wait_http.sh http://localhost:8080/loadgen/ 120

# 3. export dependencies strictly from Jaeger traces
python3 scripts/traces_to_deps.py --fail-on-empty > deps.json

# 4. build the graph (Kafka edges land in async_edges)
python3 scripts/deps_to_graph.py \
  --deps deps.json \
  --entrypoints config/entrypoints.txt \
  --out graph.json

# 5. optional: declarative replicas – by default each service has one replica
echo '{}' > replicas.json

# 6. run the model, e.g., for p_fail=0.3
python3 scripts/resilience.py \
  --graph graph.json \
  --replicas replicas.json \
  --p 0.3 \
  --samples 120000 \
  --mode all-block \
  --out model_all-block.json
python3 scripts/resilience.py \
  --graph graph.json \
  --replicas replicas.json \
  --p 0.3 \
  --samples 120000 \
  --mode async \
  --out model_async.json
```

## CI / GitHub Actions
The workflow `.github/workflows/resilience.yml` boots the demo and runs chaos experiments for the matrix:

- `p_fail ∈ {0.1, 0.3, 0.5, 0.7, 0.9}`
- `chunk ∈ {1,2}` (the same chaos level is executed twice for averaging)

Within each job:
1. Dependencies are collected **only from traces** (`scripts/traces_to_deps.py --fail-on-empty`). If Jaeger does not have traces, the job fails; there is no `/dependencies` fallback.
2. `scripts/deps_to_graph.py` produces a single graph with an `async_edges` field (all edges touching Kafka). Topology is identical for both model semantics.
3. `scripts/resilience.py` runs twice (all-block and async) on the same `graph.json`. No seeds are used — Monte Carlo relies on the natural PRNG.
4. Chaos (`scripts/compose_chaos.sh`) executes 10 windows of 60 s. Before each window all eligible containers are started, a kill set is sampled (disallowlist `config/services_disallowlist.txt` excludes frontend/infra), and victims are stopped for the full window duration. Every second we verify victims stay `exited/dead`; any restarts are logged as `anomaly` in `window_log`.
5. Each window runs 30 HTTP probes (`collect_live.py`) and computes `R_live = probe_ok / probe_total` over `/api/products`, `/api/recommendations`, `/api/cart`, `/api/checkout` (POST).
6. `scripts/summarize_results.py` aggregates model and live data; Quick report prints two mix-based tables using the actual probe distribution: “All windows” and “Clean windows (no anomaly)” (the latter excludes windows marked as anomalous).

Artifacts per matrix cell:

- `model_modeall-block_p*.json`, `model_modeasync_p*.json` – model estimates.
- `live_p*_chunk*_*.json` – individual live windows (only HTTP probes).
- `summary_p*_chunk*.json` – aggregated windows (`sum`, `sum_sq`, `windows`).
- `reports/rows_p*_chunk*.csv`, `reports/overall_p*_chunk*.json` – head-to-head statistics (delta bias, CI, Wilcoxon).
- `window_log_p*_chunk*.jsonl` – chaos logs with killed services.

Locust is used only to keep telemetry warm; it does not feed into the live metric.

## Model: all-block vs async

`scripts/resilience.py` builds a reachability graph from `graph.json`. For `mode=all-block`, all edges are used. For `mode=async`, edges listed in `async_edges` (i.e., `checkout→kafka`, `kafka→accounting`, `kafka→fraud-detection`) are removed so that the Kafka branch does not block checkout’s immediate success. Monte Carlo now mirrors `compose_chaos.sh`: each trial samples a fixed-size kill set (rounded `p_fail * #containers`) without replacement **from all services except those in** `config/services_disallowlist.txt` (entrypoints/infra are skipped). Both the graph shape and the set of kill-eligible services drive model accuracy. If `--targets-file` is provided, each simulation randomly picks one declared HTTP endpoint and checks only that target—matching how live probes rotate endpoints. If `replicas.json` is `{}`, we assume 1 replica per service.

## Live metric

`R_live` relies entirely on HTTP probes. Each window executes `probe_attempts` requests (30 in CI), randomly alternating endpoints. The checkout scenario performs a full POST workflow (`cart → checkout`). `probe_detail` logs URL, method (`GET/POST`), status, and errors. `R_live = probe_ok / probe_total`, while `R_live_sd` and `N` describe the dispersion across windows. Without seeds, chaos kill sets and probes differ each run.

## Per-endpoint targets & evaluation

`config/targets.json` defines strict success criteria per HTTP endpoint. Each entry must use exactly one rule (`any_of`, `all_of`, or `k_of_n`) and may optionally pin the entry service and mark asynchronous dependencies as skippable in async mode:

```json
{
  "GET /api/products": {
    "entry": "frontend",
    "any_of": ["product-catalog"]
  },
  "POST /api/checkout": {
    "entry": "frontend",
    "all_of": ["checkout", "cart", "payment", "shipping"],
    "exclude_async": true
  }
}
```

- `any_of` / `all_of` take a list of normalized service names.
- `k_of_n` uses `{ "k": <int>, "items": [...] }`.
- `exclude_async: true` ensures Kafka-derived edges do not block the endpoint when `--mode async`.

`scripts/resilience.py` understands two new CLI flags:

- `--targets-file config/targets.json` loads endpoint specs.
- `--endpoint "GET /api/products"` runs Monte Carlo strictly for that endpoint.
- Without `--endpoint` but with `--targets-file`, each simulation picks a random endpoint (uniform over JSON keys) and checks only that one, mirroring `collect_live.py`.
- `--disallowlist config/services_disallowlist.txt` excludes the listed services from chaos (everything else is eligible). The file includes the current OTel Demo frontend/infra services (frontend, jaeger, grafana, load-generator, opensearch, postgresql, image-provider, llm, flagd-ui, valkey-cart, etc.).

When `--endpoint` is set, the script writes `model_{mode}_e{endpoint}_p{p}_chunk{chunk}.json` and includes the endpoint label in the payload. The CI workflow iterates over every endpoint in `targets.json`, so artifacts are emitted for both `all-block` and `async` semantics.

`scripts/collect_live.py` now records per-endpoint success counts (keys match `targets.json`) in each `live_p*_chunk*_*.json` window. `scripts/summarize_results.py --targets-file config/targets.json` correlates live windows with matching model artifacts and generates:

- `reports/rows_p{p}_chunk{chunk}_e{endpoint}.csv` — one row per window with live rate, per-mode bias, and delta.
- `reports/overall_p{p}_chunk{chunk}_e{endpoint}.json` — Wilcoxon, bootstrap CI (median delta), window count, and the two model estimates.
- `reports/rows_p{p}_chunk{chunk}_mix.csv` + `reports/overall_p{p}_chunk{chunk}_mix.json` — weighted mixes where each window’s contribution is weighted by the number of probes hitting that endpoint.

The “Quick report” step in CI reads these CSVs/JSONs and prints a compact table per endpoint (plus the mix) with mean bias for both modes, delta (`async - all`), sample count, and Wilcoxon p-value. Negative delta means the async model is closer to the live metric for that endpoint.

## Notes

- Kafka edges are reconstructed from real traces: `scripts/traces_to_deps.py` looks for spans with `messaging.system=kafka` and `span.kind=producer/consumer` so that `checkout→kafka` and `kafka→consumers` appear even without parentSpanId.
- `scripts/deps_to_graph.py` never alters the service list per mode: the single graph is reused for both all-block and async semantics.
- Probe/window counts are driven by `PROBE_ATTEMPTS` and `WINDOWS` in the workflow. Larger values reduce noise in `R_live` but increase runtime.
- Seeds have been removed entirely: chaos, Monte Carlo, and bootstrap (in `summarize_results.py` we use `random.Random()` without a fixed seed) produce different results on each run.

You can run individual scripts locally (e.g., only `collect_live.py`) for debugging. No Locust API is required — everything uses standard HTTP.
