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

# Infra services to prune from the synchronous graph.
SKIP = {
    "frontend-proxy", "jaeger", "grafana", "otel-collector", "zipkin",
    "prometheus", "loadgenerator", "load-generator"
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
nodes = set()
for item in data:
    # tolerate aliases found in Jaeger deps/traces payloads
    parent = item.get("parent") or item.get("caller") or item.get("p")
    child  = item.get("child")  or item.get("callee") or item.get("c")
    if not parent or not child:
        continue
    pu, pv = norm(parent), norm(child)
    if pu == pv:
        continue
    edges.append((pu, pv))
    nodes.add(pu); nodes.add(pv)


# Determine which nodes should be treated as transparent (skip) while keeping
# entrypoints even if their normalized names match SKIP entries.
skip_nodes = {n for n in nodes if n in SKIP and n not in ENTRY_ALLOW}

from collections import defaultdict

adj = defaultdict(set)
radj = defaultdict(set)
for u, v in edges:
    adj[u].add(v)
    radj[v].add(u)

import functools

@functools.lru_cache(maxsize=None)
def expand_forward(node):
    """Return non-skip descendants reachable via only skip nodes."""
    out = set()
    for nxt in adj.get(node, ()):
        if nxt in skip_nodes:
            out |= expand_forward(nxt)
        else:
            out.add(nxt)
    return out

@functools.lru_cache(maxsize=None)
def expand_backward(node):
    """Return non-skip ancestors that reach node via only skip nodes."""
    out = set()
    for prev in radj.get(node, ()):
        if prev in skip_nodes:
            out |= expand_backward(prev)
        else:
            out.add(prev)
    return out

filtered = set()
for u, v in edges:
    su = u in skip_nodes
    sv = v in skip_nodes
    if not su and not sv:
        filtered.add((u, v))
        continue
    if not su and sv:
        targets = expand_forward(v)
        for t in targets:
            filtered.add((u, t))
        continue
    if su and not sv:
        sources = expand_backward(u)
        for s in sources:
            filtered.add((s, v))
        continue
    if su and sv:
        sources = expand_backward(u)
        targets = expand_forward(v)
        for s in sources:
            for t in targets:
                filtered.add((s, t))

# drop edges that still touch skip nodes (no reachable non-skip endpoints)
filtered = {(u, v) for (u, v) in filtered if u not in skip_nodes and v not in skip_nodes}

# build node index
V = sorted({u for u, v in filtered} | {v for u, v in filtered})
idx = {v: i for i, v in enumerate(V)}

# unique directed edges on normalized ids
edge_set = {(idx[u], idx[v]) for u, v in filtered}
E = sorted(edge_set)

kafka_idx = idx.get("kafka")
async_edges = []
if kafka_idx is not None:
    async_edges = [[u, v] for (u, v) in E if u == kafka_idx or v == kafka_idx]

# entrypoints → ids (ignore missing ones gracefully)
entry_ids = [idx[e] for e in entry if e in idx]

graph = {"services": V, "edges": E, "entrypoints": entry_ids, "async_edges": async_edges}
json.dump(graph, open(a.out, "w"))
print(f"Wrote {a.out}: |V|={len(V)} |E|={len(E)} entries={len(entry_ids)}")
if len(E) == 0:
    print("graph.json has no edges after pruning", file=sys.stderr)
    sys.exit(1)
