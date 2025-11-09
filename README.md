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
- We **keep the demo’s Locust** load generator. `R_live = 1 - (5xx + transport errors + Locust #failures)/total`, плюс дополнительный функциональный пробный checkout для гарантии: если фронт не может оформить заказ (даже при 200 OK), окно тоже считается провалом.  
- Discovery prefers scraping **Jaeger traces** with `scripts/traces_to_deps.py`; if indexing is still empty it automatically falls back to the `/jaeger/api/dependencies` endpoint.
- CI bumps Locust’s default load (`LOCUST_USERS=150`, `LOCUST_SPAWN_RATE=30`) and runs `scripts/validate_chaos_live.py` up front (60 s chaos window с задержкой 15 с, HTTP-пробник фронтенда, несколько попыток до ≥80 запросов **или** хотя бы одной неудачной HTTP-проверки при `R_live ≤ 0.99`) — это подтверждает, что контейнеры реально глушатся; итоги попадают в `validation_*.json`.
- Основной цикл хаоса использует 60‑секундные окна, даёт 15 с на “раскрытие” отказа и только после этого снимает 40‑секундное окно `collect_live.py`, чтобы измерения всегда приходились на период простоя сервисов.
- В итоговом отчёте GitHub Actions строго контролируется монотонность только для `R_model`; `R_live_mean` выводится для информации и может “прыгать” (например, если хаос не ловится загрузкой).
- Chaos is implemented as **random container stops** for a fixed window, then automatic restarts, to match a fail‑stop assumption.

See `config/services_allowlist.txt` to decide which app services are eligible for kills; infra (proxy, collector, jaeger, grafana, db/brokers) is excluded by default.
