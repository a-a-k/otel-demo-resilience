#!/usr/bin/env python3
import json, argparse, sys

ap = argparse.ArgumentParser()
ap.add_argument("--deps", required=True)
ap.add_argument("--entrypoints", required=True)
ap.add_argument("--out", required=True)
a = ap.parse_args()

raw = json.load(open(a.deps))

# Accept list or {"data":[...]}
if isinstance(raw, dict):
    data = raw.get("data", []) or []
elif isinstance(raw, list):
    data = raw
else:
    data = []

# Infra/async to prune from synchronous service graph
SKIP = {
    "frontend-proxy", "jaeger", "grafana", "otel-collector", "zipkin",
    "kafka", "kafka-server", "prometheus", "loadgenerator"
}

def norm(s: str) -> str:
    s = str(s).strip().lower().replace("_", "-")
    for suf in ("-service", "service"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s

entry = [
    norm(x)
    for x in open(a.entrypoints)
    if x.strip() and not x.startswith("#")
]
ENTRY_ALLOW = set(entry)

edges = []
for item in data:
    # tolerate aliases found in Jaeger deps/traces payloads
    parent = item.get("parent") or item.get("caller") or item.get("p")
    child  = item.get("child")  or item.get("callee") or item.get("c")
    if not parent or not child:
        continue
    pu, pv = norm(parent), norm(child)
    if (pu in SKIP and pu not in ENTRY_ALLOW) or (pv in SKIP and pv not in ENTRY_ALLOW):
        continue
    if pu == pv:
        continue
    edges.append((pu, pv))

# build node index
V = sorted({u for u, v in edges} | {v for u, v in edges})
idx = {v: i for i, v in enumerate(V)}

# unique directed edges on normalized ids
E = sorted({(idx[u], idx[v]) for u, v in edges})

# entrypoints → ids (ignore missing ones gracefully)
entry_ids = [idx[e] for e in entry if e in idx]

graph = {"services": V, "edges": E, "entrypoints": entry_ids}
json.dump(graph, open(a.out, "w"))
print(f"Wrote {a.out}: |V|={len(V)} |E|={len(E)} entries={len(entry_ids)}")
if len(E) == 0:
    print("graph.json has no edges after pruning", file=sys.stderr)
    sys.exit(1)
