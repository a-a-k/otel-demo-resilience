#!/usr/bin/env python3
import json, argparse, random, collections

def bfs_ok(adj, alive, s):
    from collections import deque
    if not alive[s]: 
        return False
    q, seen = deque([s]), {s}
    ok = False
    while q:
        u = q.popleft()
        outs = [v for v in adj[u] if alive[v]]
        if not outs:
            ok = True
        for v in outs:
            if v not in seen:
                seen.add(v); q.append(v)
    return ok

ap = argparse.ArgumentParser()
ap.add_argument("--graph", required=True)
ap.add_argument("--replicas", required=True)
ap.add_argument("--p", type=float, required=True)
ap.add_argument("--samples", type=int, default=120000)
ap.add_argument("--out", required=True)
a = ap.parse_args()

G = json.load(open(a.graph))
V = G["services"]; E = G["edges"]; entry = G["entrypoints"]
replicas = json.load(open(a.replicas))

adj = collections.defaultdict(list)
for u,v in E: adj[u].append(v)

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
    ok_any = any(bfs_ok(adj, alive, e) for e in entry)
    succ += 1 if ok_any else 0

R_model = succ / a.samples
with open(a.out, "w") as f:
    json.dump({"R_model": R_model, "p_fail": a.p, "samples": a.samples}, f)
print(json.dumps({"R_model": R_model, "samples": a.samples}))
