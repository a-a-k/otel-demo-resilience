#!/usr/bin/env python3
import json, argparse

ap = argparse.ArgumentParser()
ap.add_argument("--deps", required=True)
ap.add_argument("--entrypoints", required=True)
ap.add_argument("--out", required=True)
a = ap.parse_args()

raw = json.load(open(a.deps))
# Handle either {"data":[...]} or a bare list
edges = []
data = raw.get("data", raw)
for item in data:
    # support multiple schema variants
    parent = item.get("parent") or item.get("caller") or item.get("p")
    child  = item.get("child")  or item.get("callee") or item.get("c")
    if parent is None or child is None: 
        continue
    edges.append((str(parent), str(child)))

def norm(s): 
    s = s.strip().lower().replace("_","-")
    # simplify common suffixes to aid matching replicas/compose service keys
    for suf in ("-service","service"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s

Vset = set()
for u,v in edges:
    Vset.add(norm(u)); Vset.add(norm(v))

V = sorted(Vset)
idx = {v:i for i,v in enumerate(V)}
E = sorted({(idx[norm(u)], idx[norm(v)]) for u,v in edges})

entry = [norm(x) for x in open(a.entrypoints).read().splitlines() if x.strip()]
entry_ids = [idx[e] for e in entry if e in idx]

graph = {"services": V, "edges": E, "entrypoints": entry_ids}
with open(a.out, "w") as f:
    json.dump(graph, f)
print(f"Wrote {a.out} with {len(V)} services and {len(E)} edges")
