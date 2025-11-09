#!/usr/bin/env python3
import argparse, time, json, csv, io, re, random, os, pathlib
import requests as R
PAT_ERR_5XX = re.compile(r"^5\d\d")
PAT_TRANSPORT = re.compile(r"(timeout|connection|reset|broken pipe|read timed out)", re.I)

HEADERS = {"Accept": "application/json"}

def _json(url, timeout=6):
    try:
        r = R.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        ctype = (r.headers.get("content-type", "")).lower()
        if "application/json" in ctype:
            return r.json()
    except Exception:
        pass
    return None

def _csv(url, timeout=6):
    try:
        r = R.get(url, timeout=timeout, allow_redirects=True)
        if "text/csv" in (r.headers.get("content-type", "")).lower():
            return list(csv.DictReader(io.StringIO(r.text)))
    except Exception:
        pass
    return None

def discover_base(base, attempts=5, sleep_s=2):
    base = base.rstrip("/")
    for _ in range(max(1, attempts)):
        # Try JSON first
        for prefix in ("", "/api", "/ui/api"):
            if _json(f"{base}{prefix}/stats/requests"):
                return (prefix, "json")
        # Fall back to CSV endpoints
        for prefix in ("", "/api", "/ui/api"):
            if _csv(f"{base}{prefix}/stats/requests/csv"):
                return (prefix, "csv")
    if attempts > 1:
        time.sleep(max(0.5, sleep_s))
    raise RuntimeError("Locust endpoints not reachable as JSON or CSV")

def totals_json(base, prefix):
    stats = _json(f"{base}{prefix}/stats/requests") or {}
    fails = _json(f"{base}{prefix}/stats/failures") or []
    exc   = _json(f"{base}{prefix}/exceptions") or []
    stats_total = (stats.get("stats_total") or {})

    # total requests: prefer stats_total, else 'Total' row, else sum
    total = stats_total.get("num_requests", 0) or 0
    items = (stats.get("stats") or [])
    for it in items:
        if it.get("name") == "Total":
            total = it.get("num_requests", 0); break
        total += it.get("num_requests", 0)

    # 5xx from failures list
    flist = (fails.get("failures") if isinstance(fails, dict) else fails) or []
    five_xx = sum((f.get("occurrences", 0) or 0) for f in flist
                  if PAT_ERR_5XX.match(str(f.get("error", ""))))
    # transport/timeouts from exceptions
    elist = (exc.get("exceptions") if isinstance(exc, dict) else exc) or []
    transport = sum((e.get("count", 0) or 0) for e in elist
                    if PAT_TRANSPORT.search(str(e.get("msg",""))))
    failures = stats_total.get("num_failures", 0) or 0
    p95 = float(stats_total.get("current_response_time_percentile_95") or stats_total.get("response_time_percentile_95") or 0)
    median = float(stats_total.get("median_response_time") or 0)
    return dict(total=total, five_xx=five_xx, transport=transport, failures=failures), {"p95": p95, "median": median}

def totals_csv(base, prefix):
    req_rows = _csv(f"{base}{prefix}/stats/requests/csv") or []
    fail_rows = _csv(f"{base}{prefix}/stats/failures/csv") or []
    exc_rows = _csv(f"{base}{prefix}/exceptions/csv") or []

    # requests.csv has '# requests' and '# failures'; prefer 'Total' row if present
    total = 0
    failures = 0
    median = 0.0
    p95 = 0.0
    for r in req_rows:
        name = (r.get("Name") or r.get("name") or "").strip()
        num = int(float(r.get("# requests") or r.get("Requests") or 0))
        fail = int(float(r.get("# failures") or r.get("Failures") or 0))
        if name.lower() == "total":
            total = num
            failures = fail
            try:
                median = float(r.get("Median") or r.get("median") or median)
                p95 = float(r.get("95%") or r.get("95%ile") or p95)
            except Exception:
                pass
            break
        total += num
        failures += fail
        try:
            median = float(r.get("Median") or r.get("median") or median)
            p95 = float(r.get("95%") or r.get("95%ile") or p95)
        except Exception:
            pass

    five_xx = 0
    for f in fail_rows:
        err = str(f.get("Error") or f.get("error") or "")
        occ = int(float(f.get("Occurrences") or f.get("occurrences") or 0))
        if re.match(r"^5\d\d", err): five_xx += occ

    transport = 0
    for e in exc_rows:
        msg = str(e.get("Message") or e.get("message") or e.get("msg") or "")
        cnt = int(float(e.get("Count") or e.get("count") or 0))
        if re.search(r"(timeout|connection|reset|broken pipe|read timed out)", msg, re.I):
            transport += cnt

    return dict(total=total, five_xx=five_xx, transport=transport, failures=failures), {"p95": p95, "median": median}

def snapshot(base):
    prefix, mode = discover_base(base)
    if mode == "json":
        return totals_json(base, prefix)
    return totals_csv(base, prefix)

def diff(a,b):
    return {k: max(0, b.get(k,0) - a.get(k,0)) for k in set(a)|set(b)}

PROBE_ENDPOINTS = ["/api/products", "/api/recommendations", "/api/cart"]
PROBE_PRODUCTS = [
    "OLJCESPC7Z", "66VCHSJNUP", "9SIQT8TOJO", "1YMWWN1N4O",
    "L9ECAV7KIM", "2ZYFJ3GM2N", "0PUK6V6EV0", "LS4PSXUNUM"
]
PROBE_CHECKOUT = {
    "email": "resilience@example.com",
    "streetAddress": "107 SW 7TH ST",
    "city": "Miami",
    "state": "FL",
    "zipCode": "33130",
    "country": "USA",
    "firstName": "Resilience",
    "lastName": "Bot",
    "creditCard": {
        "creditCardNumber": "4432-8015-6152-0454",
        "creditCardExpirationMonth": 1,
        "creditCardExpirationYear": 2030,
        "creditCardCvv": 672
    }
}

def frontend_probe(base_url, attempts=2, timeout=6):
    base = base_url.rstrip("/")
    ok = 0
    detail = []
    for _ in range(max(1, attempts)):
        endpoint = random.choice(PROBE_ENDPOINTS + ["/api/cart/checkout"])
        url = f"{base}{endpoint}"
        try:
            if endpoint == "/api/cart/checkout":
                session = R.Session()
                item = random.choice(PROBE_PRODUCTS)
                add = session.post(f"{base}/api/cart", json={"item": {"productId": item, "quantity": 1}}, timeout=timeout)
                add.raise_for_status()
                payload = dict(PROBE_CHECKOUT)
                payload["email"] = f"resilience+{random.randint(1,999999)}@example.com"
                resp = session.post(url, json=payload, timeout=timeout)
            else:
                resp = R.get(url, timeout=timeout)
            if 200 <= resp.status_code < 300:
                pass
            elif resp.status_code in (401, 403):
                detail.append({"endpoint": url, "status": "skip", "code": resp.status_code})
                ok += 1
                continue
            else:
                resp.raise_for_status()
            data = {}
            try:
                data = resp.json()
            except ValueError:
                data = {}
            if endpoint == "/api/cart/checkout":
                order_id = data.get("orderId") or data.get("order", {}).get("orderId")
                if not order_id:
                    raise RuntimeError("checkout missing orderId")
            elif not data:
                raise RuntimeError("empty response")
            ok += 1
            detail.append({"endpoint": url, "status": "ok"})
        except Exception as exc:
            detail.append({"endpoint": url, "status": "fail", "error": str(exc)})
    return ok, attempts, detail

def read_window_log(path):
    data = []
    try:
        with open(path) as fh:
            for line in fh:
                line=line.strip()
                if not line: continue
                try:
                    data.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return data

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--locust", required=True)    # e.g., http://localhost:8080/loadgen
    ap.add_argument("--window", type=int, default=30)
    ap.add_argument("--latency-p95-threshold", type=float, default=1500.0,
                    help="If latest 95th percentile latency (ms) exceeds this, mark window unhealthy.")
    ap.add_argument("--probe-frontend", default="http://localhost:8080",
                    help="Frontend base URL for functional probes (empty to disable).")
    ap.add_argument("--probe-attempts", type=int, default=2)
    ap.add_argument("--window-log", default="window_log.jsonl",
                    help="Path to window log emitted by compose_chaos.sh; used for annotations.")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    counters0, _ = snapshot(a.locust)
    time.sleep(max(1, a.window))
    counters1, meta1 = snapshot(a.locust)
    # remove latency/meta keys from counters if present accidentally
    d = diff(counters0, counters1)

    failures_other = max(0, d.get("failures", 0) - d.get("five_xx", 0))
    bad = d.get("five_xx", 0) + d.get("transport", 0) + failures_other
    good = max(0, d["total"] - bad)
    R_live = (good / d["total"]) if d["total"] else 0.0
    latency_bad = False
    if meta1.get("p95", 0) >= a.latency_p95_threshold:
        latency_bad = True
        R_live = 0.0
    zero_traffic = d["total"] <= 0
    if zero_traffic:
        R_live = 0.0

    detail = dict(d)
    detail["failures_other"] = failures_other
    detail["latency_p95"] = meta1.get("p95")
    detail["latency_median"] = meta1.get("median")
    detail["latency_bad"] = latency_bad
    detail["zero_traffic"] = zero_traffic

    probe_detail = []
    if a.probe_frontend:
        ok_probe, total_probe, probe_detail = frontend_probe(a.probe_frontend, a.probe_attempts)
        detail["probe_ok"] = ok_probe
        detail["probe_total"] = total_probe
        detail["probe_detail"] = probe_detail
        if total_probe > 0:
            R_live = min(R_live, ok_probe / total_probe)
            detail["probe_ratio"] = ok_probe / total_probe

    killed_services = []
    last_log = {}
    if a.window_log:
        entries = read_window_log(a.window_log)
        if entries:
            last_log = entries[-1]
            killed_services = last_log.get("services") or []
    detail["killed_services"] = killed_services
    out = {"R_live": R_live, "detail": detail, "window_log": last_log}
    with open(a.out, "w") as f: json.dump(out, f)
    print(json.dumps(out))
