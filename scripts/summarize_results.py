#!/usr/bin/env python3
"""Summarize model vs live metrics for a single (p_fail, chunk) run."""
import argparse
import csv
import glob
import json
import math
import os
import random
import statistics
from typing import Any, Dict, List, Optional, Tuple


def load_json(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_live_values(pattern: str) -> Tuple[List[str], List[float]]:
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(f"No live files matched pattern {pattern}")
    values = []
    for path in files:
        data = load_json(path)
        if "R_live" not in data:
            raise ValueError(f"Missing R_live in {path}")
        values.append(float(data["R_live"]))
    return files, values


def bootstrap_ci(
    values: List[float],
    n_resamples: int = 10000,
    alpha: float = 0.05,
    seed: Optional[int] = None,
    statistic: str = "mean",
) -> Tuple[Optional[float], Optional[float]]:
    if not values:
        return None, None
    rng = random.Random(seed)
    n = len(values)
    stats = []
    for _ in range(n_resamples):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        if statistic == "median":
            stats.append(statistics.median(sample))
        else:
            stats.append(sum(sample) / n)
    stats.sort()
    lower_idx = int((alpha / 2) * n_resamples)
    upper_idx = min(n_resamples - 1, int((1 - alpha / 2) * n_resamples))
    return stats[lower_idx], stats[upper_idx]


def wilcoxon_signed_rank(x: List[float], y: List[float]) -> Optional[float]:
    diffs = [a - b for a, b in zip(x, y) if a != b]
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


def norm(s: str) -> str:
    return str(s).strip().lower().replace("_", "-")


def safe_endpoint_label(endpoint: str) -> str:
    out = endpoint.strip().replace("/", "_").replace(" ", "_")
    cleaned = "_".join(seg for seg in out.split("_") if seg)
    return cleaned.lower() if cleaned else "endpoint"


def load_targets_map(path: str) -> Dict[str, Dict[str, str]]:
    data = load_json(path)
    if not isinstance(data, dict):
        raise ValueError("targets.json must be an object")
    return {endpoint: spec for endpoint, spec in data.items()}


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def write_csv(path: str, header: List[str], rows: List[List[Any]]) -> None:
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def collect_endpoint_rows(
    endpoint: str,
    safe_label: str,
    model_block: float,
    model_async: float,
    live_records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    rows = []
    for record in live_records:
        per_endpoint = record["data"].get("per_endpoint") or {}
        stats = per_endpoint.get(endpoint)
        if not stats:
            continue
        total = int(stats.get("total", 0))
        ok = int(stats.get("ok", 0))
        if total <= 0:
            continue
        r_live = ok / total
        bias_block = abs(model_block - r_live)
        bias_async = abs(model_async - r_live)
        rows.append(
            {
                "window": record["index"],
                "endpoint": endpoint,
                "safe_label": safe_label,
                "r_live": r_live,
                "bias_block": bias_block,
                "bias_async": bias_async,
                "delta": bias_async - bias_block,
                "weight": total,
            }
        )
    return rows


def summarize_rows(
    endpoint: str,
    rows: List[Dict[str, Any]],
    model_block: float,
    model_async: float,
    p_fail: str,
    chunk: str,
    graph_hash: Optional[str],
    model_seed: Optional[int],
) -> Dict[str, Any]:
    bias_block_vals = [row["bias_block"] for row in rows]
    bias_async_vals = [row["bias_async"] for row in rows]
    delta_vals = [row["delta"] for row in rows]
    mean_bias_block = sum(bias_block_vals) / len(rows) if rows else 0.0
    mean_bias_async = sum(bias_async_vals) / len(rows) if rows else 0.0
    delta_mean = sum(delta_vals) / len(rows) if rows else 0.0
    delta_median = statistics.median(delta_vals) if rows else 0.0
    ci_low, ci_high = bootstrap_ci(delta_vals, seed=model_seed, statistic="median")
    wilcoxon_p = wilcoxon_signed_rank(bias_block_vals, bias_async_vals) if rows else None
    return {
        "endpoint": endpoint,
        "p_fail": p_fail,
        "chunk": chunk,
        "graph_hash": graph_hash,
        "n_windows": len(rows),
        "mean_bias_all_block": mean_bias_block,
        "mean_bias_async": mean_bias_async,
        "delta_bias_mean": delta_mean,
        "delta_bias_median": delta_median,
        "ci95_delta_bias": [ci_low, ci_high] if ci_low is not None else None,
        "wilcoxon_pvalue": wilcoxon_p,
        "model_seed": model_seed,
        "R_model_all_block": model_block,
        "R_model_async": model_async,
    }


def write_endpoint_reports(
    endpoint: str,
    safe_label: str,
    rows: List[Dict[str, Any]],
    summary: Dict[str, Any],
    p_fail: str,
    chunk: str,
) -> None:
    rows_path = f"reports/rows_p{p_fail}_chunk{chunk}_e{safe_label}.csv"
    rows_data = [
        [
            row["window"],
            p_fail,
            chunk,
            endpoint,
            row["r_live"],
            summary["R_model_all_block"],
            summary["R_model_async"],
            row["bias_block"],
            row["bias_async"],
            row["delta"],
            row["weight"],
        ]
        for row in rows
    ]
    write_csv(
        rows_path,
        [
            "window",
            "p_fail",
            "chunk",
            "endpoint",
            "R_live",
            "R_model_all_block",
            "R_model_async",
            "bias_all_block",
            "bias_async",
            "delta_bias",
            "weight",
        ],
        rows_data,
    )
    overall_path = f"reports/overall_p{p_fail}_chunk{chunk}_e{safe_label}.json"
    ensure_dir(os.path.dirname(overall_path) or ".")
    with open(overall_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)


def collect_mix_rows(
    endpoints: List[str],
    models: Dict[str, Dict[str, float]],
    live_records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    rows = []
    for record in live_records:
        per_endpoint = record["data"].get("per_endpoint") or {}
        contributions = []
        total_attempts = 0
        for endpoint in endpoints:
            stats = per_endpoint.get(endpoint)
            if not stats or endpoint not in models:
                continue
            total = int(stats.get("total", 0))
            ok = int(stats.get("ok", 0))
            if total <= 0:
                continue
            contributions.append((endpoint, total, ok))
            total_attempts += total
        if total_attempts == 0:
            continue
        r_live_mix = 0.0
        r_model_block = 0.0
        r_model_async = 0.0
        for endpoint, total, ok in contributions:
            weight = total / total_attempts
            r_live_mix += weight * (ok / total)
            r_model_block += weight * models[endpoint]["block"]
            r_model_async += weight * models[endpoint]["async"]
        bias_block = abs(r_model_block - r_live_mix)
        bias_async = abs(r_model_async - r_live_mix)
        rows.append(
            {
                "window": record["index"],
                "endpoint": "mix",
                "safe_label": "mix",
                "r_live": r_live_mix,
                "bias_block": bias_block,
                "bias_async": bias_async,
                "delta": bias_async - bias_block,
                "weight": total_attempts,
                "R_model_all_block": r_model_block,
                "R_model_async": r_model_async,
            }
        )
    return rows


def write_mix_reports(
    rows: List[Dict[str, Any]],
    summary: Dict[str, Any],
    p_fail: str,
    chunk: str,
) -> None:
    rows_path = f"reports/rows_p{p_fail}_chunk{chunk}_mix.csv"
    rows_data = [
        [
            row["window"],
            p_fail,
            chunk,
            "mix",
            row["r_live"],
            row["R_model_all_block"],
            row["R_model_async"],
            row["bias_block"],
            row["bias_async"],
            row["delta"],
            row["weight"],
        ]
        for row in rows
    ]
    write_csv(
        rows_path,
        [
            "window",
            "p_fail",
            "chunk",
            "endpoint",
            "R_live",
            "R_model_all_block",
            "R_model_async",
            "bias_all_block",
            "bias_async",
            "delta_bias",
            "weight",
        ],
        rows_data,
    )
    overall_path = f"reports/overall_p{p_fail}_chunk{chunk}_mix.json"
    ensure_dir(os.path.dirname(overall_path) or ".")
    with open(overall_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--p-fail", required=True)
    parser.add_argument("--chunk", required=True)
    parser.add_argument("--rows-out", default="reports/rows.csv")
    parser.add_argument("--overall-out", default="reports/overall.json")
    parser.add_argument(
        "--live-pattern",
        help="Glob for live R_live files; defaults to live_p{p}_chunk{chunk}_*.json",
    )
    parser.add_argument("--targets-file", help="Path to config/targets.json for endpoint analysis.")
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
    live_pattern = args.live_pattern or f"live_p{p_fail}_chunk{chunk}_*.json"
    live_files, r_live_values = read_live_values(live_pattern)
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

    ci_low, ci_high = bootstrap_ci(delta_windows, seed=model_seed)
    wilcoxon_p = wilcoxon_signed_rank(bias_block_windows, bias_async_windows)
    delta_effect = cliffs_delta(bias_block_windows, bias_async_windows)

    row_dir = os.path.dirname(args.rows_out) or "."
    ensure_dir(row_dir)
    write_csv(
        args.rows_out,
        [
            "p_fail",
            "chunk",
            "graph_hash",
            "R_live_mean",
            "R_model_all_block",
            "R_model_async",
            "bias_block_mean",
            "bias_async_mean",
            "delta_bias_mean",
            "windows",
            "model_seed",
        ],
        [
            [
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
            ]
        ],
    )

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
        "R_model_all_block": r_model_block,
        "R_model_async": r_model_async,
    }

    overall_dir = os.path.dirname(args.overall_out) or "."
    ensure_dir(overall_dir)
    with open(args.overall_out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    print(json.dumps(summary))

    # Per-endpoint analysis
    if args.targets_file:
        targets_map = load_targets_map(args.targets_file)
        endpoints = sorted(targets_map.keys())
        live_records = [
            {"index": idx + 1, "path": path, "data": load_json(path)}
            for idx, path in enumerate(live_files)
        ]
        endpoint_models: Dict[str, Dict[str, float]] = {}
        for endpoint in endpoints:
            safe_label = safe_endpoint_label(endpoint)
            block_path = f"model_modeall-block_e{safe_label}_p{p_fail}_chunk{chunk}.json"
            async_path = f"model_modeasync_e{safe_label}_p{p_fail}_chunk{chunk}.json"
            block_data = load_json(block_path)
            async_data = load_json(async_path)
            block_val = float(block_data["R_model"])
            async_val = float(async_data["R_model"])
            endpoint_models[endpoint] = {"block": block_val, "async": async_val}

            rows = collect_endpoint_rows(endpoint, safe_label, block_val, async_val, live_records)
            summary_endpoint = summarize_rows(
                endpoint,
                rows,
                block_val,
                async_val,
                p_fail,
                chunk,
                graph_hash,
                model_seed,
            )
            write_endpoint_reports(endpoint, safe_label, rows, summary_endpoint, p_fail, chunk)

        if endpoint_models:
            mix_rows = collect_mix_rows(endpoints, endpoint_models, live_records)
            mix_model_block = (
                statistics.mean(row["R_model_all_block"] for row in mix_rows) if mix_rows else 0.0
            )
            mix_model_async = (
                statistics.mean(row["R_model_async"] for row in mix_rows) if mix_rows else 0.0
            )
            mix_summary = summarize_rows(
                "mix",
                mix_rows,
                mix_model_block,
                mix_model_async,
                p_fail,
                chunk,
                graph_hash,
                model_seed,
            )
            write_mix_reports(mix_rows, mix_summary, p_fail, chunk)


if __name__ == "__main__":
    main()
