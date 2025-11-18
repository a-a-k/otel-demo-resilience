#!/usr/bin/env python3
import argparse
import json
import os
import random
from collections import deque
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True)
    ap.add_argument("--replicas", required=True)
    ap.add_argument("--p", type=float, required=True)
    ap.add_argument("--samples", type=int, default=120000)
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--mode",
        choices=["all-block", "async"],
        default="all-block",
        help="Failure semantics: block on all edges or treat async edges (kafka) as non-blocking.",
    )
    ap.add_argument(
        "--targets",
        help="Optional file with newline-separated service names treated as required sinks.",
    )
    ap.add_argument(
        "--targets-file",
        help="Optional JSON file with per-endpoint target specifications.",
    )
    ap.add_argument(
        "--endpoint",
        help="Endpoint label from the targets JSON; enables per-endpoint success semantics.",
    )
    return ap.parse_args()


def norm(s: str) -> str:
    return str(s).strip().lower().replace("_", "-")


def safe_endpoint_label(endpoint: str) -> str:
    out = endpoint.strip().replace("/", "_").replace(" ", "_")
    return "_".join([seg for seg in out.split("_") if seg]).lower() or "endpoint"


def load_targets(path: str) -> Dict[str, Dict[str, Any]]:
    """
    Load endpoint target definitions from config/targets.json.

    Each entry must declare exactly one of {any_of, all_of, k_of_n}.
    Service names and entrypoints are normalized to graph naming (lowercase, '-' separators).
    Returns a mapping endpoint -> validated spec with keys:
      - endpoint (original label)
      - entry (normalized or None)
      - rule (any_of|all_of|k_of_n)
      - targets / items / k (depending on rule)
      - exclude_async (bool)
    """
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise ValueError("targets.json must contain a JSON object")
    specs: Dict[str, Dict[str, Any]] = {}
    for endpoint, spec in raw.items():
        if not isinstance(spec, dict):
            raise ValueError(f"Endpoint '{endpoint}' must map to an object")
        normalized_spec: Dict[str, Any] = {"endpoint": endpoint}
        entry = spec.get("entry")
        normalized_spec["entry"] = norm(entry) if entry else None
        normalized_spec["exclude_async"] = bool(spec.get("exclude_async", False))

        rule_fields = [key for key in ("any_of", "all_of", "k_of_n") if key in spec]
        if len(rule_fields) != 1:
            raise ValueError(
                f"Endpoint '{endpoint}' must declare exactly one of any_of/all_of/k_of_n"
            )
        rule = rule_fields[0]
        normalized_spec["rule"] = rule
        if rule in ("any_of", "all_of"):
            values = spec[rule]
            if not isinstance(values, list) or not values:
                raise ValueError(f"Endpoint '{endpoint}' -> {rule} must be a non-empty list")
            normalized_spec["targets"] = [norm(item) for item in values]
        else:
            block = spec["k_of_n"]
            if not isinstance(block, dict):
                raise ValueError(f"Endpoint '{endpoint}' -> k_of_n must be an object")
            try:
                k_val = int(block.get("k", 0))
            except (TypeError, ValueError):
                raise ValueError(f"Endpoint '{endpoint}' -> k_of_n.k must be an integer")
            items = block.get("items")
            if not isinstance(items, list) or not items:
                raise ValueError(f"Endpoint '{endpoint}' -> k_of_n.items must be a non-empty list")
            normalized_spec["k"] = k_val
            normalized_spec["items"] = [norm(item) for item in items]
        specs[endpoint] = normalized_spec
    return specs


def get_endpoint_spec(targets: Dict[str, Dict[str, Any]], endpoint: str) -> Dict[str, Any]:
    """
    Fetch a normalized endpoint spec from the target map.
    Raises KeyError if the endpoint label is unknown.
    """
    if endpoint not in targets:
        raise KeyError(f"Endpoint '{endpoint}' not found in targets.json")
    spec = dict(targets[endpoint])
    spec["_endpoint_key"] = endpoint
    return spec


def prepare_graph(graph: Dict[str, Any]) -> None:
    """Attach adjacency and lookup helpers to the graph dictionary for fast access."""
    services = graph.get("services") or []
    edges = graph.get("edges") or []
    adj: List[List[int]] = [[] for _ in range(len(services))]
    for u, v in edges:
        if isinstance(u, int) and isinstance(v, int):
            if 0 <= u < len(services) and 0 <= v < len(services):
                adj[u].append(v)
    async_edges = set()
    for pair in graph.get("async_edges") or []:
        if (
            isinstance(pair, (list, tuple))
            and len(pair) == 2
            and isinstance(pair[0], int)
            and isinstance(pair[1], int)
        ):
            async_edges.add((pair[0], pair[1]))
    graph["_adj_all"] = adj
    graph["_async_edge_set"] = async_edges
    graph["_name_to_idx"] = {norm(name): idx for idx, name in enumerate(services)}


def draw_alive(services: List[str], replicas: Dict[str, Any], p_fail: float) -> List[bool]:
    alive = [False] * len(services)
    for idx, svc in enumerate(services):
        replicas_count = int(replicas.get(svc, 1))
        survivors = 0
        for _ in range(max(1, replicas_count)):
            if random.random() > p_fail:
                survivors += 1
        alive[idx] = survivors > 0
    return alive


def bfs_reachable(
    entry_idx: int,
    adjacency: List[List[int]],
    failed_idx: Optional[Set[int]] = None,
    banned_edges: Optional[Set[Tuple[int, int]]] = None,
) -> Set[int]:
    """Return the set of nodes reachable from entry_idx while skipping failed nodes and banned edges."""
    if entry_idx < 0 or entry_idx >= len(adjacency):
        return set()
    if failed_idx and entry_idx in failed_idx:
        return set()
    seen = {entry_idx}
    q = deque([entry_idx])
    while q:
        u = q.popleft()
        if failed_idx and u in failed_idx:
            continue
        for v in adjacency[u]:
            if failed_idx and v in failed_idx:
                continue
            if banned_edges and (u, v) in banned_edges:
                continue
            if v not in seen:
                seen.add(v)
                q.append(v)
    return seen


def endpoint_success(
    graph: Dict[str, Any],
    failed_services: Set[str],
    endpoint_spec: Dict[str, Any],
    mode: str,
) -> bool:
    """
    Decide success of a single Monte Carlo trial for a specific HTTP endpoint.
    - graph: parsed graph.json with keys: nodes, edges, async_edges, entrypoints
    - failed_services: set of services down in this trial (after replicas logic)
    - endpoint_spec: one of {any_of|all_of|k_of_n} (+ optional exclude_async)
    - mode: "all-block" | "async"
    Semantics:
      * Build an adjacency view excluding edges incident to failed services.
      * If mode == "async" and endpoint_spec.get("exclude_async", False):
          remove edges listed in graph["async_edges"] from reachability.
      * Let src be the entry service for the endpoint (use explicit spec entry,
        fall back to the first entrypoint, or to 'frontend' when available).
      * For any_of / all_of / k_of_n decide success based on reachability
        from src to target services in endpoint_spec.
    """
    services = graph.get("services") or []
    adjacency = graph.get("_adj_all") or []
    name_to_idx = graph.get("_name_to_idx") or {}
    async_edges = graph.get("_async_edge_set") or set()

    if not adjacency:
        return False

    entry_name = endpoint_spec.get("entry") or "frontend"
    entry_idx = name_to_idx.get(entry_name, None)
    entrypoints = graph.get("entrypoints") or []
    if entry_idx is None:
        if isinstance(entrypoints, dict):
            maybe = entrypoints.get(endpoint_spec.get("_endpoint_key"))
            if isinstance(maybe, int) and 0 <= maybe < len(services):
                entry_idx = maybe
        elif entrypoints:
            first = entrypoints[0]
            if isinstance(first, int) and 0 <= first < len(services):
                entry_idx = first
    if entry_idx is None and "frontend" in name_to_idx:
        entry_idx = name_to_idx["frontend"]
    if entry_idx is None:
        raise ValueError("Unable to resolve entry service for endpoint evaluation")

    failed_idx = {
        name_to_idx[name]
        for name in failed_services
        if name in name_to_idx and isinstance(name_to_idx[name], int)
    }

    exclude_async = mode == "async" and endpoint_spec.get("exclude_async", False)
    banned_edges = async_edges if exclude_async else None
    reachable = bfs_reachable(entry_idx, adjacency, failed_idx=failed_idx, banned_edges=banned_edges)
    structural_required: Optional[Set[int]] = None
    if exclude_async:
        structural_required = bfs_reachable(entry_idx, adjacency, failed_idx=None, banned_edges=banned_edges)

    def targets_to_indices(items: Iterable[str]) -> List[int]:
        idxs = []
        for item in items:
            idx = name_to_idx.get(item)
            if idx is None:
                raise ValueError(f"Target service '{item}' not found in graph")
            idxs.append(idx)
        return idxs

    def filter_structural(nodes: List[int]) -> Tuple[List[int], int]:
        if structural_required is None:
            return nodes, 0
        required = [node for node in nodes if node in structural_required]
        excluded = len(nodes) - len(required)
        return required, excluded

    rule = endpoint_spec["rule"]
    if rule in ("any_of", "all_of"):
        node_ids = targets_to_indices(endpoint_spec["targets"])
        node_ids, _ = filter_structural(node_ids)
        if not node_ids:
            return True
        if rule == "any_of":
            return any(node in reachable for node in node_ids)
        return all(node in reachable for node in node_ids)

    if rule == "k_of_n":
        node_ids = targets_to_indices(endpoint_spec["items"])
        node_ids, excluded = filter_structural(node_ids)
        required_k = max(0, int(endpoint_spec.get("k", 0)) - excluded)
        if required_k <= 0:
            return True
        if not node_ids:
            return False
        satisfied = sum(1 for node in node_ids if node in reachable)
        return satisfied >= min(required_k, len(node_ids))

    raise ValueError(f"Unsupported rule '{rule}' for endpoint success evaluation")


def main() -> None:
    args = parse_args()
    graph = json.load(open(args.graph, "r", encoding="utf-8"))
    prepare_graph(graph)
    services = graph["services"]
    entrypoints_raw = graph.get("entrypoints") or []
    entry = [idx for idx in entrypoints_raw if isinstance(idx, int) and idx < len(services)]
    replicas = json.load(open(args.replicas, "r", encoding="utf-8"))

    targets_simple = set()
    if args.targets:
        try:
            name_to_idx = graph["_name_to_idx"]
            with open(args.targets, "r", encoding="utf-8") as fh:
                names = [
                    norm(line)
                    for line in fh
                    if line.strip() and not line.startswith("#")
                ]
            targets_simple = {name_to_idx[name] for name in names if name in name_to_idx}
        except FileNotFoundError:
            targets_simple = set()

    adj_all = graph["_adj_all"]
    async_edges = graph["_async_edge_set"]
    adj_for_mode: List[List[int]] = [[] for _ in range(len(services))]
    for u, neighbors in enumerate(adj_all):
        if args.mode == "async":
            adj_for_mode[u] = [v for v in neighbors if (u, v) not in async_edges]
        else:
            adj_for_mode[u] = list(neighbors)
    sinks = [len(adj_for_mode[i]) == 0 for i in range(len(services))]
    if targets_simple:
        sinks = [i in targets_simple for i in range(len(services))]

    endpoint_spec: Optional[Dict[str, Any]] = None
    if args.endpoint:
        if not args.targets_file:
            raise SystemExit("--endpoint requires --targets-file")
        targets_map = load_targets(args.targets_file)
        try:
            endpoint_spec = get_endpoint_spec(targets_map, args.endpoint)
        except KeyError as exc:
            raise SystemExit(str(exc))

    def bfs_ok(alive: List[bool], start: int) -> bool:
        if start >= len(alive) or not alive[start]:
            return False
        q, seen = deque([start]), {start}
        while q:
            u = q.popleft()
            if sinks[u]:
                return True
            for v in adj_for_mode[u]:
                if alive[v] and v not in seen:
                    seen.add(v)
                    q.append(v)
        return False

    def draw() -> List[bool]:
        return draw_alive(services, replicas, args.p)

    successes = 0
    for _ in range(args.samples):
        alive = draw()
        if endpoint_spec:
            failed = {services[i] for i, ok in enumerate(alive) if not ok}
            try:
                trial_ok = endpoint_success(graph, failed, endpoint_spec, args.mode)
            except ValueError as exc:
                raise SystemExit(str(exc))
        else:
            trial_ok = any(bfs_ok(alive, e) for e in entry)
        successes += 1 if trial_ok else 0

    r_model = successes / args.samples if args.samples else 0.0
    graph_hash = os.getenv("GRAPH_SHA256")
    result: Dict[str, Any] = {
        "R_model": r_model,
        "p_fail": args.p,
        "samples": args.samples,
        "mode": args.mode,
    }
    if args.endpoint:
        result["endpoint"] = args.endpoint
    if graph_hash:
        result["graph_hash"] = graph_hash
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    summary = {"R_model": r_model, "samples": args.samples}
    if graph_hash:
        summary["graph_hash"] = graph_hash
    if args.endpoint:
        summary["endpoint"] = args.endpoint
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
