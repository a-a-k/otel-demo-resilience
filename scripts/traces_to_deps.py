#!/usr/bin/env python3
import os, time, json, urllib.parse, urllib.request

BASES = [f"http://localhost:{os.getenv('ENVOY_PORT','8080')}/jaeger/api",
         f"http://localhost:{os.getenv('ENVOY_PORT','8080')}/jaeger/ui/api"]  # try both base paths
LOOKBACK_MIN = int(os.getenv("LOOKBACK_MINUTES","30"))
END_MS = int(time.time()*1000)
LOOKBACK_MS = LOOKBACK_MIN*60*1000

def get_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"Accept":"application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def first_ok(paths):
    for p in paths:
        try: return get_json(p)
        except Exception: pass
    return None

# 1) list services
svc_resp = first_ok([f"{b}/services" for b in BASES]) or {"data":[]}
services = svc_resp.get("data", svc_resp if isinstance(svc_resp, list) else [])

# 2) pull recent traces per service and accumulate edges
edges = {}
for svc in services:
    svcq = urllib.parse.quote(str(svc))
    lookback_m = f"{max(1,LOOKBACK_MS//60000)}m"  # e.g., "30m"
    # query params per Jaeger UI JSON (service, lookback, end, limit). :contentReference[oaicite:1]{index=1}
    urls = [f"{b}/traces?service={svcq}&lookback={lookback_m}&end={END_MS}&limit=40" for b in BASES]
    data = first_ok(urls) or {}
    for trace in data.get("data", []):
        procs = trace.get("processes", {})  # span.processID -> {serviceName: "..."}
        spans = trace.get("spans", [])
        # spanID -> serviceName
        svc_by_span = {}
        for sp in spans:
            pid = sp.get("processID")
            name = (procs.get(pid, {}) or {}).get("serviceName")
            if name: svc_by_span[sp.get("spanID")] = name
        # parent->child edges by references
        for sp in spans:
            child = svc_by_span.get(sp.get("spanID"))
            for ref in sp.get("references", []):
                if ref.get("refType") in ("CHILD_OF","FOLLOWS_FROM"):
                    parent = svc_by_span.get(ref.get("spanID"))
                    if parent and child and parent != child:
                        edges[(parent, child)] = edges.get((parent, child), 0) + 1

out = [{"parent": a, "child": b, "callCount": n} for (a,b), n in edges.items()]
print(json.dumps(out))
