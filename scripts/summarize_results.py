#!/usr/bin/env python3
"""Summarize model vs live metrics for a single (p_fail, chunk) run.

Outputs per-chunk rows.csv and an overall JSON with bootstrap CI and Wilcoxon p-value.
"""
import argparse, csv, glob, json, math, os, random
from typing import List, Optional


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_live_values(pattern: str) -> List[float]:
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(f"No live files matched pattern {pattern}")
    values = []
    for path in files:
        data = load_json(path)
        if "R_live" not in data:
            raise ValueError(f"Missing R_live in {path}")
        values.append(float(data["R_live"]))
    return values


def read_chaos_seed(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    seed = None
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "chaos_seed" in rec:
                seed = str(rec["chaos_seed"])
    return seed


def bootstrap_mean_ci(values: List[float], n_resamples: int = 10000,
                      alpha: float = 0.05, seed: Optional[int] = None):
    if not values:
        return None, None
    rng = random.Random(seed)
    n = len(values)
    stats = []
    for _ in range(n_resamples):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        stats.append(sum(sample) / n)
    stats.sort()
    lower_idx = int((alpha / 2) * n_resamples)
    upper_idx = min(n_resamples - 1, int((1 - alpha / 2) * n_resamples))
    return stats[lower_idx], stats[upper_idx]


def wilcoxon_signed_rank(x: List[float], y: List[float]) -> Optional[float]:
    diffs = [a - b for a, b in zip(x, y) if a != b]
    # Remove zeros
    diffs = [d for d in diffs if d != 0]
    n = len(diffs)
    if n == 0:
        return None
    abs_with_idx = sorted((abs(d), idx) for idx, d in enumerate(diffs))
    ranks = [0.0] * n
    tie_counts = []
    i = 0
    while i < n:
        j = i
        while j < n and abs_with_idx[j][0] == abs_with_idx[i][0]:
            j += 1
        rank = (i + 1 + j) / 2
        for k in range(i, j):
            ranks[abs_with_idx[k][1]] = rank
        tie_len = j - i
        if tie_len > 1:
            tie_counts.append(tie_len)
        i = j
    w_pos = sum(rank for rank, diff in zip(ranks, diffs) if diff > 0)
    w_neg = sum(rank for rank, diff in zip(ranks, diffs) if diff < 0)
    w = min(w_pos, w_neg)
    mean = n * (n + 1) / 4
    var = n * (n + 1) * (2 * n + 1) / 24
    if tie_counts:
        tie_term = sum(t * (t * t - 1) for t in tie_counts) / 48
        var -= tie_term
    if var <= 0:
        return None
    z = (w - mean) / math.sqrt(var)
    # two-sided p-value using normal approximation
    return math.erfc(abs(z) / math.sqrt(2))


def cliffs_delta(x: List[float], y: List[float]) -> Optional[float]:
    if not x or not y:
        return None
    n = len(x)
    m = len(y)
    greater = 0
    for a in x:
        for b in y:
            if a > b:
                greater += 1
            elif a < b:
                greater -= 1
    return greater / (n * m)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--p-fail", required=True)
    parser.add_argument("--chunk", required=True)
    parser.add_argument("--rows-out", default="reports/rows.csv")
    parser.add_argument("--overall-out", default="reports/overall.json")
    parser.add_argument("--mode", default="all-block")  # unused placeholder
    args = parser.parse_args()

    p_fail = args.p_fail
    chunk = args.chunk

    model_block = load_json(f"model_modeall-block_p{p_fail}.json")
    model_async = load_json(f"model_modeasync_p{p_fail}.json")

    graph_hash_block = model_block.get("graph_hash")
    graph_hash_async = model_async.get("graph_hash")
    if graph_hash_block and graph_hash_async and graph_hash_block != graph_hash_async:
        raise SystemExit("Graph hash mismatch between modes")
    graph_hash = graph_hash_block or graph_hash_async

    model_seed = model_block.get("seed")
    chaos_seed = read_chaos_seed(
        f"window_log_modeall-block_p{p_fail}_chunk{chunk}.jsonl"
    )

    live_pattern = f"live_modeall-block_p{p_fail}_chunk{chunk}_*.json"
    r_live_values = read_live_values(live_pattern)
    windows = len(r_live_values)
    if windows == 0:
        raise SystemExit("No live windows found")

    r_model_block = float(model_block["R_model"])
    r_model_async = float(model_async["R_model"])

    bias_block_windows = [abs(r_model_block - x) for x in r_live_values]
    bias_async_windows = [abs(r_model_async - x) for x in r_live_values]
    delta_windows = [bb - ba for bb, ba in zip(bias_block_windows, bias_async_windows)]

    r_live_mean = sum(r_live_values) / windows
    bias_block_mean = sum(bias_block_windows) / windows
    bias_async_mean = sum(bias_async_windows) / windows
    delta_mean = bias_block_mean - bias_async_mean
    share_positive = sum(1 for d in delta_windows if d > 0) / windows

    ci_low, ci_high = bootstrap_mean_ci(delta_windows, seed=model_seed)
    wilcoxon_p = wilcoxon_signed_rank(bias_block_windows, bias_async_windows)
    delta_effect = cliffs_delta(bias_block_windows, bias_async_windows)

    row_dir = os.path.dirname(args.rows_out) or "."
    os.makedirs(row_dir, exist_ok=True)
    with open(args.rows_out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "p_fail", "chunk", "graph_hash", "R_live_mean",
            "R_model_all_block", "R_model_async", "bias_block_mean",
            "bias_async_mean", "delta_bias_mean", "windows",
            "model_seed", "chaos_seed"
        ])
        writer.writerow([
            p_fail,
            chunk,
            graph_hash or "",
            r_live_mean,
            r_model_block,
            r_model_async,
            bias_block_mean,
            bias_async_mean,
            delta_mean,
            windows,
            model_seed if model_seed is not None else "",
            chaos_seed if chaos_seed is not None else ""
        ])

    summary = {
        "p_fail": p_fail,
        "chunk": chunk,
        "graph_hash": graph_hash,
        "windows": windows,
        "mean_R_live": r_live_mean,
        "mean_bias_block": bias_block_mean,
        "mean_bias_async": bias_async_mean,
        "mean_delta_bias": delta_mean,
        "share_delta_bias_positive": share_positive,
        "ci95_delta_bias": [ci_low, ci_high] if ci_low is not None else None,
        "wilcoxon_pvalue": wilcoxon_p,
        "cliffs_delta": delta_effect,
        "model_seed": model_seed,
        "chaos_seed": chaos_seed,
    }

    overall_dir = os.path.dirname(args.overall_out) or "."
    os.makedirs(overall_dir, exist_ok=True)
    with open(args.overall_out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    print(json.dumps(summary))


if __name__ == "__main__":
    main()
