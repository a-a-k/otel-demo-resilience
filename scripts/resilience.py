#!/usr/bin/env python3
import json, argparse, random
from collections import deque

ap = argparse.ArgumentParser()
ap.add_argument("--graph", required=True)
ap.add_argument("--replicas", required=True)
ap.add_argument("--p", type=float, required=True)
ap.add_argument("--samples", type=int, default=120000)
ap.add_argument("--out", required=True)
ap.add_argument("--targets", help="Optional file with newline-separated service names treated as required sinks.")
a = ap.parse_args()

G = json.load(open(a.graph))
V = G["services"]; E = G["edges"]; entry = G["entrypoints"]
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
    if u < len(V) and v < len(V):
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
with open(a.out, "w") as f:
    json.dump({"R_model": R_model, "p_fail": a.p, "samples": a.samples}, f)
print(json.dumps({"R_model": R_model, "samples": a.samples}))
