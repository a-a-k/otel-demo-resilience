#!/usr/bin/env python3
import json, argparse

ap = argparse.ArgumentParser()
ap.add_argument("--deps", required=True)
ap.add_argument("--entrypoints", required=True)
ap.add_argument("--out", required=True)
a = ap.parse_args()

raw = json.load(open(a.deps))

# Accept either [{"parent","child","callCount"}, ...] OR {"data":[...]}
if isinstance(raw, dict):
    data = raw.get("data", [])
elif isinstance(raw, list):
    data = raw
else:
    data = []

SKIP = {
    "frontend-proxy","jaeger","grafana","otel-collector","zipkin",
    "kafka","kafka-server","prometheus","loadgenerator"
}

edges = []
for item in data:
    # tolerate aliases found in Jaeger deps/traces payloads
    parent = item.get("parent") or item.get("caller") or item.get("p")
    child  = item.get("child")  or item.get("callee") or item.get("c")
    if not parent or not child:
        continue
    parent, child = str(parent), str(child)
    def norm(s): 
        s=s.strip().lower().replace("_","-"); 

def norm(s: str) -> str:
    s = s.strip().lower().replace("_", "-")
    for suf in ("-service", "service"):  # normalize common suffixes
        if s.endswith(suf): s = s[: -len(suf)]
    return s

# build node index
Vset = {norm(u) for u, v in edges} | {norm(v) for u, v in edges}
V = sorted(Vset)
idx = {v: i for i, v in enumerate(V)}

# unique directed edges on normalized ids
E = sorted({(idx[norm(u)], idx[norm(v)]) for u, v in edges})

# entrypoints → ids (ignore missing ones gracefully)
entry = [norm(x) for x in open(a.entrypoints) if x.strip() and not x.startswith("#")]
entry_ids = [idx[e] for e in entry if e in idx]

graph = {"services": V, "edges": E, "entrypoints": entry_ids}
json.dump(graph, open(a.out, "w"))
print(f"Wrote {a.out}: |V|={len(V)} |E|={len(E)} entries={len(entry_ids)}")
