#!/usr/bin/env python3
# Reliable warm-up: ensure Locust has served some requests (JSON preferred, CSV fallback).
# If Locust remains cold, probe Jaeger for any recent traces.
# Usage: python3 scripts/warmup.py --locust http://localhost:8080/loadgen --timeout 300
import argparse, time, sys, os, json, csv, io, urllib.request, urllib.parse

DEF_ENVOY=os.getenv("ENVOY_PORT","8080")

HDR_JSON={"Accept":"application/json"}


def _get(url, timeout=6, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ctype=(r.headers.get("Content-Type","") or "").lower()
        data=r.read()
        return ctype, data


def locust_total(base):
    base = base.rstrip("/")
    # JSON first
    for prefix in ("", "/api", "/ui/api"):
        try:
            ctype, data = _get(f"{base}{prefix}/stats/requests", headers=HDR_JSON)
            if "application/json" in ctype:
                obj = json.loads(data.decode("utf-8","replace"))
                total = (obj.get("stats_total") or {}).get("num_requests", 0) or 0
                if total:
                    return total
                items = obj.get("stats") or []
                # Try explicit Total row else sum
                for it in items:
                    if (it.get("name") or "").strip() == "Total":
                        total = it.get("num_requests", 0) or 0
                        break
                    total += it.get("num_requests", 0) or 0
                return total
        except Exception:
            pass
    # CSV fallback
    for prefix in ("", "/api", "/ui/api"):
        try:
            ctype, data = _get(f"{base}{prefix}/stats/requests/csv")
            if "text/csv" in ctype:
                rows = list(csv.DictReader(io.StringIO(data.decode("utf-8","replace"))))
                total = 0
                for r in rows:
                    name = (r.get("Name") or r.get("name") or "").strip()
                    num = int(float(r.get("# requests") or r.get("Requests") or 0))
                    if name.lower() == "total":
                        total = num
                        break
                    total += num
                return total
        except Exception:
            pass
    return 0


def jaeger_has_traces(bases):
    now_ms = int(time.time()*1000)
    for b in bases:
        b = b.rstrip("/")
        try:
            ctype, data = _get(f"{b}/services", headers=HDR_JSON)
            if "application/json" not in ctype:
                continue
            obj = json.loads(data.decode("utf-8","replace"))
            if isinstance(obj, list):
                services = obj
            else:
                services = obj.get("data",[]) or []
            services = [str(s) for s in services if s]
            if not services:
                continue
            # Query a small sample of services for any recent traces
            for svc in services[:5]:
                qs = f"service={urllib.parse.quote(svc)}&lookback=15m&end={now_ms}&limit=5"
                try:
                    ctype2, data2 = _get(f"{b}/traces?{qs}", headers=HDR_JSON)
                except Exception:
                    continue
                if "application/json" in ctype2:
                    obj2 = json.loads(data2.decode("utf-8","replace"))
                    if (obj2.get("data") or []):
                        return True
        except Exception:
            continue
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--locust", default=f"http://localhost:{DEF_ENVOY}/loadgen")
    ap.add_argument("--jaeger", default=",".join([
        f"http://localhost:{DEF_ENVOY}/jaeger/api",
        f"http://localhost:{DEF_ENVOY}/jaeger/ui/api",
        "http://localhost:16686/api",
    ]))
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args()

    deadline = time.time() + max(1, a.timeout)
    bases = [b.strip() for b in a.jaeger.split(",") if b.strip()]

    while time.time() < deadline:
        try:
            total = locust_total(a.locust)
            if total and total > 0:
                print(f"Warm-up OK: Locust total requests={total}")
                return 0
        except Exception:
            pass
        # Try Jaeger traces as fallback
        try:
            if jaeger_has_traces(bases):
                print("Warm-up OK: Jaeger shows recent traces")
                return 0
        except Exception:
            pass
        time.sleep(5)
    print("Warm-up failed: no Locust traffic or recent Jaeger traces detected", file=sys.stderr)
    return 1

if __name__ == "__main__":
    sys.exit(main())
