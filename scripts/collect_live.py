#!/usr/bin/env python3
import argparse, time, json, re
import requests as R

def fetch(locust_base):
    s = R.get(f"{locust_base}/stats/requests").json()
    f = R.get(f"{locust_base}/stats/failures").json()
    e = R.get(f"{locust_base}/exceptions").json()
    return s, f, e

def totals_from(stats, fails, exc):
    total = stats.get("stats_total", {}).get("num_requests", 0) or 0
    # 5xx failures from failures feed
    five_xx = sum(r.get("occurrences", 0) for r in fails if re.match(r"^5\\d\\d", r.get("error","")))
    # transport/timeouts from exceptions feed
    transport = sum(item.get("count", 0) for item in exc.get("exceptions", [])
                    if re.search(r"(timeout|time[- ]?out|ConnectionError|Connection reset|Broken pipe|Read timed out)",
                                 item.get("msg",""), re.I))
    return dict(total=total, five_xx=five_xx, transport=transport)

def diff(a,b):
    return {k: max(0, b.get(k,0)-a.get(k,0)) for k in set(a)|set(b)}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--locust", required=True)      # e.g., http://localhost:8080/loadgen
    ap.add_argument("--window", type=int, default=30)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    s0,f0,e0 = fetch(a.locust)
    t0 = totals_from(s0,f0,e0)
    time.sleep(max(1, a.window))
    s1,f1,e1 = fetch(a.locust)
    t1 = totals_from(s1,f1,e1)
    d = diff(t0,t1)

    bad = d["five_xx"] + d["transport"]
    good = max(0, d["total"] - bad)
    R_live = (good / d["total"]) if d["total"] else 0.0

    out = {"R_live": R_live, "detail": d}
    with open(a.out, "w") as f:
        json.dump(out, f)
    print(json.dumps(out))
