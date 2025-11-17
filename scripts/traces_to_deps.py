#!/usr/bin/env python3
# Build service-level edges directly from Jaeger traces (traces-only mode).
# Probes the OTel demo proxy bases and the direct Jaeger port if exposed.
import argparse, os, time, json, urllib.parse, urllib.request, sys

ENVOY_PORT = os.getenv("ENVOY_PORT","8080")
LOOKBACK_MIN = int(os.getenv("LOOKBACK_MINUTES","30"))
BASES = [b.strip().rstrip("/") for b in os.getenv(
    "JAEGER_BASES",
    f"http://localhost:{ENVOY_PORT}/jaeger/api,"
    f"http://localhost:{ENVOY_PORT}/jaeger/ui/api,"
    "http://localhost:16686/api"
).split(",") if b.strip()]

HDR = {"Accept":"application/json"}
TRACE_SAMPLES = []
TRACE_SAMPLE_LIMIT = 10
FETCH_STATS = {b: {"errors": 0, "empty": 0, "traces": 0} for b in BASES}

def log(msg: str) -> None:
    print(msg, file=sys.stderr)

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

def fetch_edges(services, lookback_min=None, limit=1000):
    edges = {}
    lookback_m = f"{max(1, lookback_min if lookback_min is not None else LOOKBACK_MIN)}m"
    end_us = int(time.time()*1_000_000)
    for svc in services:
        qs = f"service={urllib.parse.quote(svc)}&lookback={lookback_m}&end={end_us}&limit={int(limit)}"
        for b in BASES:
            try:
                payload = get_json(f"{b}/traces?{qs}")
            except Exception:
                FETCH_STATS[b]["errors"] += 1
                continue
            if not payload:
                FETCH_STATS[b]["empty"] += 1
                continue
            traces = payload.get("data") or []
            if not traces:
                FETCH_STATS[b]["empty"] += 1
                continue
            FETCH_STATS[b]["traces"] += len(traces)
            for trace in traces:
                procs = trace.get("processes", {})
                spans = trace.get("spans", [])
                # spanID -> serviceName
                svc_by_span = {}
                for sp in spans:
                    pid = sp.get("processID")
                    name = (procs.get(pid) or {}).get("serviceName")
                    if name:
                        svc_by_span[sp.get("spanID")] = name
                if len(TRACE_SAMPLES) < TRACE_SAMPLE_LIMIT:
                    sample = {
                        "traceID": trace.get("traceID") or trace.get("traceid") or "",
                        "services": sorted({s for s in svc_by_span.values() if s}),
                        "spanCount": len(spans)
                    }
                    TRACE_SAMPLES.append(sample)
                added = 0
                # edges via references or parentSpanId
                for sp in spans:
                    child = svc_by_span.get(sp.get("spanID"))
                    parent = None
                    for ref in sp.get("references", []):
                        if ref.get("refType") in ("CHILD_OF", "FOLLOWS_FROM"):
                            parent = svc_by_span.get(ref.get("spanID"))
                            if parent:
                                break
                    if not parent and sp.get("parentSpanId"):
                        parent = svc_by_span.get(sp.get("parentSpanId"))
                    if parent and child and parent != child:
                        edges[(parent, child)] = edges.get((parent, child), 0) + 1
                        added += 1
                # Fallback: if no cross-service references, infer by time-ordered service transitions
                if added == 0 and spans:
                    seq = []
                    for sp in spans:
                        svcname = svc_by_span.get(sp.get("spanID"))
                        if not svcname:
                            continue
                        ts = sp.get("startTime") or sp.get("startTimeMillis") or sp.get("startTimeUnixNano") or 0
                        try:
                            ts = int(ts)
                        except Exception:
                            ts = 0
                        seq.append((ts, svcname))
                    if seq:
                        seq.sort()
                        # compress consecutive duplicates
                        ordered = []
                        last = None
                        for _, sname in seq:
                            if sname != last:
                                ordered.append(sname)
                                last = sname
                        for i in range(len(ordered)-1):
                            u, v = ordered[i], ordered[i+1]
                            if u != v:
                                edges[(u, v)] = edges.get((u, v), 0) + 1
            break  # next service after first base that returned
    return [{"parent": a, "child": b, "callCount": n} for (a, b), n in edges.items()]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fail-on-empty", action="store_true",
                    help="Fail if trace scraping produced zero edges (default behavior).")
    args = ap.parse_args()

    svcs = discover_services()
    if svcs:
        log(f"Discovered services from Jaeger: {svcs}")
    # Fallback: try common OTel Demo backend services if /services is empty
    if not svcs:
        log("Jaeger /services returned empty; falling back to predefined service list")
        svcs = [
            'checkoutservice','productcatalogservice','cartservice','paymentservice',
            'recommendationservice','shippingservice','currencyservice','adservice',
            'emailservice','fraudservice','accountingservice','frontend','frontend-proxy'
        ]
    # Retry for a bounded timeout to wait for Jaeger indexing
    timeout = int(os.getenv("TRACES_TIMEOUT", "300"))
    deadline = time.time() + max(1, timeout)
    widened = False
    while time.time() < deadline:
        out = fetch_edges(svcs, lookback_min=None, limit=1000)
        if not out and not widened and (deadline - time.time()) < timeout*0.5:
            # Widen search window once if still empty: 60 minutes
            widened = True
            out = fetch_edges(svcs, lookback_min=max(LOOKBACK_MIN, 60), limit=1000)
        if out:
            print(json.dumps(out))
            return
        time.sleep(5)
    # Final widened attempt before aborting
    out = fetch_edges(svcs, lookback_min=max(LOOKBACK_MIN, 60), limit=1000)
    if out:
        print(json.dumps(out))
        return
    log("Trace scraping found zero edges; exiting (traces-only mode)." +
        (" (--fail-on-empty flag set)" if args.fail_on_empty else ""))
    if TRACE_SAMPLES:
        log("Sample of traces observed despite zero edges: " + json.dumps(TRACE_SAMPLES, indent=2))
    else:
        log("No traces were returned from Jaeger during scraping attempts.")
    if FETCH_STATS:
        log("Fetch stats per base: " + json.dumps(FETCH_STATS, indent=2))
    print("[]")
    sys.exit(1)

if __name__ == "__main__":
    main()
