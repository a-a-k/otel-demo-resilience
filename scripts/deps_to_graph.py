#!/usr/bin/env python3
import json, argparse

ap = argparse.ArgumentParser()
ap.add_argument("--deps", required=True)
ap.add_argument("--entrypoints", required=True)
ap.add_argument("--out", required=True)
a = ap.parse_args()

raw = json.load(open(a.deps))

# Accept list or {"data":[...]}
data = raw.get("data", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])

SKIP = {
    "frontend-proxy","jaeger","grafana","otel-collector","zipkin",
    "kafka","kafka-server","prometheus","loadgenerator"
}

edges = []
SKIP = {"frontend-proxy", "jaeger", "grafana", "otel-collector", "kafka", "kafka-server", "prometheus",
        "loadgenerator", "zipkin"}
for item in data:
    # tolerate aliases found in Jaeger deps/traces payloads
    parent = item.get("parent") or item.get("caller") or item.get("p")
    child  = item.get("child")  or item.get("callee") or item.get("c")
    if norm(parent) in SKIP or norm(child) in SKIP: continue
    parent, child = str(parent), str(child)
    def norm(s): 
        s=s.strip().lower().replace("_","-");
        # scripts/deps_to_graph.py (inside the edge loop)

SKIP = {
    "frontend-proxy","frontend","jaeger","grafana","otel-collector","zipkin",
    "kafka","kafka-server","prometheus","loadgenerator"
}

def norm(s: str) -> str:
    s = s.strip().lower().replace("_", "-")
    for suf in ("-service", "service"):  # normalize common suffixes
        if s.endswith(suf): s = s[: -len(suf)]
    return s

edges = []
for item in data:
    parent = item.get("parent") or item.get("caller") or item.get("p")
    child  = item.get("child")  or item.get("callee") or item.get("c")
    if not parent or not child: 
        continue
    pu, pv = norm(str(parent)), norm(str(child))
    if pu in SKIP or pv in SKIP: 
        continue
    edges.append((pu, pv))

# build node index
V = sorted({u for u, v in edges} | {v for u, v in edges})
idx = {v: i for i, v in enumerate(V)}

# unique directed edges on normalized ids
E = sorted({(idx[u], idx[v]) for u, v in edges})

# entrypoints → ids (ignore missing ones gracefully)
entry = [norm(x) for x in open(a.entrypoints) if x.strip() and not x.startswith("#")]
entry_ids = [idx[e] for e in entry if e in idx]

graph = {"services": V, "edges": E, "entrypoints": entry_ids}
json.dump(graph, open(a.out, "w"))
print(f"Wrote {a.out}: |V|={len(V)} |E|={len(E)} entries={len(entry_ids)}")
