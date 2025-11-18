#!/usr/bin/env python3
import json, argparse, random, os
from collections import deque

ap = argparse.ArgumentParser()
ap.add_argument("--graph", required=True)
ap.add_argument("--replicas", required=True)
ap.add_argument("--p", type=float, required=True)
ap.add_argument("--samples", type=int, default=120000)
ap.add_argument("--out", required=True)
ap.add_argument("--mode", choices=["all-block", "async"], default="all-block",
                help="Failure semantics: block on all edges or treat async edges (kafka) as non-blocking.")
ap.add_argument("--targets", help="Optional file with newline-separated service names treated as required sinks.")
ap.add_argument("--seed", type=int, help="Optional PRNG seed for deterministic Monte Carlo draws.")
a = ap.parse_args()

seed = a.seed
if seed is not None:
    random.seed(seed)

G = json.load(open(a.graph))
V = G["services"]; E = G["edges"]; entry = G["entrypoints"]
async_edges_raw = G.get("async_edges") or []
async_edges = set()
for pair in async_edges_raw:
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        continue
    try:
        u = int(pair[0]); v = int(pair[1])
    except (TypeError, ValueError):
        continue
    async_edges.add((u, v))
replicas = json.load(open(a.replicas))

def norm(s: str) -> str:
    return str(s).strip().lower().replace("_", "-")

target_set = set()
if a.targets:
    try:
        names = [
            norm(line)
            for line in open(a.targets)
            if line.strip() and not line.startswith("#")
        ]
        name_to_idx = {norm(name): idx for idx, name in enumerate(V)}
        target_set = {name_to_idx[n] for n in names if n in name_to_idx}
    except FileNotFoundError:
        target_set = set()

entry = [e for e in entry if e < len(V)]

adj = [[] for _ in range(len(V))]
for u, v in E:
    if not isinstance(u, int) or not isinstance(v, int):
        continue
    if 0 <= u < len(V) and 0 <= v < len(V):
        if a.mode == "async" and (u, v) in async_edges:
            continue
        adj[u].append(v)
sinks = [len(adj[i]) == 0 for i in range(len(V))]
if target_set:
    sinks = [i in target_set for i in range(len(V))]

def bfs_ok(alive, start):
    if start >= len(alive) or not alive[start]:
        return False
    q, seen = deque([start]), {start}
    while q:
        u = q.popleft()
        if sinks[u]:
            return True
        for v in adj[u]:
            if alive[v] and v not in seen:
                seen.add(v)
                q.append(v)
    return False

def draw_alive():
    alive = [False]*len(V)
    for i,s in enumerate(V):
        r = int(replicas.get(s, 1))
        surv = 0
        for _ in range(max(1,r)):
            if random.random() > a.p: surv += 1
        alive[i] = (surv>0)
    return alive

succ = 0
for _ in range(a.samples):
    alive = draw_alive()
    ok_any = any(bfs_ok(alive, e) for e in entry)
    succ += 1 if ok_any else 0

R_model = succ / a.samples
graph_hash = os.getenv("GRAPH_SHA256")
result = {"R_model": R_model, "p_fail": a.p, "samples": a.samples}
if seed is not None:
    result["seed"] = seed
if graph_hash:
    result["graph_hash"] = graph_hash
with open(a.out, "w") as f:
    json.dump(result, f)
summary = {"R_model": R_model, "samples": a.samples}
if seed is not None:
    summary["seed"] = seed
if graph_hash:
    summary["graph_hash"] = graph_hash
print(json.dumps(summary))
