#!/usr/bin/env python3
# Build service-level edges directly from Jaeger traces (no /dependencies).
# Probes the OTel demo proxy bases and the direct Jaeger port if exposed.
import os, time, json, urllib.parse, urllib.request, sys

ENVOY_PORT = os.getenv("ENVOY_PORT","8080")
LOOKBACK_MIN = int(os.getenv("LOOKBACK_MINUTES","30"))
END_MS = int(time.time()*1000)
BASES = [b.strip().rstrip("/") for b in os.getenv(
    "JAEGER_BASES",
    f"http://localhost:{ENVOY_PORT}/jaeger/api,"
    f"http://localhost:{ENVOY_PORT}/jaeger/ui/api,"
    "http://localhost:16686/api"
).split(",") if b.strip()]

HDR = {"Accept":"application/json"}

def get_json(url, timeout=8):
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ctype = (r.headers.get("Content-Type","") or "").lower()
        if "application/json" not in ctype:
            return None
        return json.loads(r.read().decode("utf-8","replace"))

def discover_services():
    # Wait until Jaeger reports at least 1 service
    for _ in range(48):  # up to ~4 min
        for b in BASES:
            try:
                data = get_json(f"{b}/services")
                if not data: continue
                if isinstance(data,list):
                    svcs = data
                else:
                    svcs = data.get("data",[]) or []
                if svcs:
                    # string-ify and uniq
                    return sorted({str(s) for s in svcs})
            except Exception:
                pass
        time.sleep(5)
    return []

def fetch_edges(services):
    edges = {}
    lookback_m = f"{max(1, LOOKBACK_MIN)}m"
    for svc in services:
        qs = f"service={urllib.parse.quote(svc)}&lookback={lookback_m}&end={END_MS}&limit=200"
        got_any = False
        for b in BASES:
            try:
                payload = get_json(f"{b}/traces?{qs}")
            except Exception:
                continue
            if not payload: 
                continue
            got_any = True
            for trace in payload.get("data", []):
                procs = trace.get("processes", {})
                spans = trace.get("spans", [])
                # spanID -> serviceName
                svc_by_span = {}
                for sp in spans:
                    pid = sp.get("processID")
                    name = (procs.get(pid) or {}).get("serviceName")
                    if name: svc_by_span[sp.get("spanID")] = name
                # edges via references or parentSpanId
                for sp in spans:
                    child = svc_by_span.get(sp.get("spanID"))
                    parent = None
                    for ref in sp.get("references", []):
                        if ref.get("refType") in ("CHILD_OF","FOLLOWS_FROM"):
                            parent = svc_by_span.get(ref.get("spanID"))
                            if parent: break
                    if not parent and sp.get("parentSpanId"):
                        parent = svc_by_span.get(sp.get("parentSpanId"))
                    if parent and child and parent != child:
                        edges[(parent, child)] = edges.get((parent, child), 0) + 1
            break  # next service after first base that returned
    return [{"parent":a, "child":b, "callCount":n} for (a,b),n in edges.items()]

def main():
    svcs = discover_services()
    if not svcs:
        print("[]")
        return
    out = fetch_edges(svcs)
    print(json.dumps(out))

if __name__ == "__main__":
    main()

