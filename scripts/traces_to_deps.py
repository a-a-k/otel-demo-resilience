#!/usr/bin/env python3
# Build service-level edges directly from Jaeger traces (no /dependencies when possible).
# Probes the OTel demo proxy bases and the direct Jaeger port if exposed, and falls back
# to Jaeger's /dependencies endpoint only when trace scraping cannot discover edges.
import argparse, os, time, json, urllib.parse, urllib.request, sys

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
    end_ms = int(time.time()*1000)
    for svc in services:
        qs = f"service={urllib.parse.quote(svc)}&lookback={lookback_m}&end={end_ms}&limit={int(limit)}"
        for b in BASES:
            try:
                payload = get_json(f"{b}/traces?{qs}")
            except Exception:
                continue
            if not payload:
                continue
            for trace in payload.get("data", []):
                procs = trace.get("processes", {})
                spans = trace.get("spans", [])
                # spanID -> serviceName
                svc_by_span = {}
                for sp in spans:
                    pid = sp.get("processID")
                    name = (procs.get(pid) or {}).get("serviceName")
                    if name:
                        svc_by_span[sp.get("spanID")] = name
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

def fetch_dependencies_edges():
    """Fallback: read Jaeger's /dependencies endpoint if traces produced no edges."""
    lookback_ms = max(1, LOOKBACK_MIN) * 60 * 1000
    end_ts = int(time.time() * 1000)
    for b in BASES:
        try:
            payload = get_json(f"{b}/dependencies?endTs={end_ts}&lookback={lookback_ms}")
        except Exception:
            continue
        if not payload:
            continue
        if isinstance(payload, dict):
            rows = payload.get("data") or []
        elif isinstance(payload, list):
            rows = payload
        else:
            rows = []
        edges = []
        for item in rows:
            parent = item.get("parent") or item.get("caller") or item.get("p")
            child = item.get("child") or item.get("callee") or item.get("c")
            if not parent or not child or parent == child:
                continue
            callcount = item.get("callCount") or item.get("calls") or item.get("count") or 0
            edges.append({"parent": parent, "child": child, "callCount": callcount})
        if edges:
            return edges
    return []

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fail-on-empty", action="store_true",
                    help="Fail if trace scraping produced zero edges (disables /dependencies fallback).")
    args = ap.parse_args()

    svcs = discover_services()
    # Fallback: try common OTel Demo backend services if /services is empty
    if not svcs:
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
    # Final attempt via traces before falling back
    out = fetch_edges(svcs, lookback_min=max(LOOKBACK_MIN, 60), limit=1000)
    if out:
        print(json.dumps(out))
        return
    if args.fail_on_empty:
        log("Trace scraping found zero edges and fallback disabled (--fail-on-empty).")
        print("[]")
        sys.exit(1)
    log("Trace scraping found zero edges; falling back to Jaeger /dependencies.")
    deps_edges = fetch_dependencies_edges()
    if deps_edges:
        print(json.dumps(deps_edges))
        return
    log("Dependencies endpoint also returned no edges.")
    print("[]")
    sys.exit(1)

if __name__ == "__main__":
    main()
