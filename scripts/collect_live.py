#!/usr/bin/env python3
import argparse, time, json, csv, io, re
import requests as R

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

def discover_base(base):
    # Try JSON first
    for prefix in ("", "/api", "/ui/api"):
        if _json(f"{base}{prefix}/stats/requests"):
            return (prefix, "json")
    # Fall back to CSV endpoints
    for prefix in ("", "/api", "/ui/api"):
        if _csv(f"{base}{prefix}/stats/requests/csv"):
            return (prefix, "csv")
    raise RuntimeError("Locust endpoints not reachable as JSON or CSV")

def totals_json(base, prefix):
    stats = _json(f"{base}{prefix}/stats/requests") or {}
    fails = _json(f"{base}{prefix}/stats/failures") or {"failures":[]}
    exc   = _json(f"{base}{prefix}/exceptions") or {"exceptions":[]}

    # total requests (prefer 'Total' row; else sum)
    items = stats.get("stats", [])
    total = 0
    for it in items:
        if it.get("name") == "Total":
            total = it.get("num_requests", 0); break
        total += it.get("num_requests", 0)

    # 5xx from failures list
    five_xx = sum(f.get("occurrences", 0) for f in fails.get("failures", fails)
                  if re.match(r"^5\d\d", str(f.get("error",""))))
    # transport/timeouts from exceptions
    transport = sum(e.get("count", 0) for e in exc.get("exceptions", [])
                    if re.search(r"(timeout|connection|reset|broken pipe|read timed out)",
                                 str(e.get("msg","")), re.I))
    return dict(total=total, five_xx=five_xx, transport=transport)

def totals_csv(base, prefix):
    req_rows = _csv(f"{base}{prefix}/stats/requests/csv") or []
    fail_rows = _csv(f"{base}{prefix}/stats/failures/csv") or []
    exc_rows = _csv(f"{base}{prefix}/exceptions/csv") or []

    # requests.csv has '# requests' and '# failures'; prefer 'Total' row if present
    total = 0
    for r in req_rows:
        name = (r.get("Name") or r.get("name") or "").strip()
        num = int(float(r.get("# requests") or r.get("Requests") or 0))
        if name.lower() == "total":
            total = num; break
        total += num

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

    return dict(total=total, five_xx=five_xx, transport=transport)

def snapshot(base):
    prefix, mode = discover_base(base)
    if mode == "json":
        return totals_json(base, prefix)
    return totals_csv(base, prefix)

def diff(a,b):
    return {k: max(0, b.get(k,0) - a.get(k,0)) for k in set(a)|set(b)}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--locust", required=True)    # e.g., http://localhost:8080/loadgen
    ap.add_argument("--window", type=int, default=30)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    s0 = snapshot(a.locust)
    time.sleep(max(1, a.window))
    s1 = snapshot(a.locust)
    d = diff(s0, s1)

    bad = d["five_xx"] + d["transport"]
    good = max(0, d["total"] - bad)
    R_live = (good / d["total"]) if d["total"] else 0.0

    out = {"R_live": R_live, "detail": d}
    with open(a.out, "w") as f: json.dump(out, f)
    print(json.dumps(out))
