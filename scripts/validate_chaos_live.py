#!/usr/bin/env python3
"""
Orchestrate a quick validation window:
- stop services via compose_chaos.sh (p_fail usually 1.0 to guarantee at least one kill)
- record Locust live stats during the outage
- assert that the chaos run actually killed something and that R_live dropped below a threshold
The resulting summary is written to --summary and supporting files (--log, --live) can be archived.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def read_json_lines(path: Path):
    rows = []
    if not path.exists():
        return rows
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--locust", required=True)
    ap.add_argument("--allowlist", required=True)
    ap.add_argument("--window", type=int, default=30)
    ap.add_argument("--p-fail", type=float, default=1.0)
    ap.add_argument("--log", default="validation_window_log.jsonl")
    ap.add_argument("--live", default="validation_live.json")
    ap.add_argument("--summary", default="validation_summary.json")
    ap.add_argument("--min-kills", type=int, default=1)
    ap.add_argument("--max-live", type=float, default=0.99)
    args = ap.parse_args()

    log_path = Path(args.log)
    live_path = Path(args.live)
    summary_path = Path(args.summary)

    for path in (log_path, live_path, summary_path):
        if path.exists():
            path.unlink()

    chaos_cmd = [
        "bash",
        "scripts/compose_chaos.sh",
        str(args.p_fail),
        args.allowlist,
        str(args.window),
        str(log_path),
    ]
    collect_cmd = [
        "python3",
        "scripts/collect_live.py",
        "--locust",
        args.locust,
        "--window",
        str(args.window),
        "--out",
        str(live_path),
    ]

    chaos_proc = subprocess.Popen(chaos_cmd)
    try:
        subprocess.run(collect_cmd, check=True)
    finally:
        chaos_rc = chaos_proc.wait()
        if chaos_rc != 0:
            print(f"compose_chaos.sh exited with {chaos_rc}", file=sys.stderr)

    entries = read_json_lines(log_path)
    if not entries:
        print(f"No chaos entries recorded in {log_path}", file=sys.stderr)
        return 1
    last = entries[-1]
    killed = int(last.get("killed") or 0)
    eligible = int(last.get("eligible") or 0)

    with live_path.open() as fh:
        live = json.load(fh)
    r_live = float(live.get("R_live") or 0.0)

    summary = {
        "window_s": args.window,
        "p_fail": args.p_fail,
        "min_kills": args.min_kills,
        "max_live": args.max_live,
        "eligible": eligible,
        "killed": killed,
        "services": last.get("services"),
        "R_live": r_live,
        "detail": live.get("detail"),
    }
    with summary_path.open("w") as fh:
        json.dump(summary, fh)
    print(json.dumps(summary))

    if killed < args.min_kills:
        print(
            f"Validation failed: expected at least {args.min_kills} kills, got {killed}",
            file=sys.stderr,
        )
        return 1
    if eligible <= 0:
        print("Validation failed: no eligible services detected for chaos", file=sys.stderr)
        return 1
    if r_live > args.max_live:
        print(
            f"Validation failed: R_live={r_live:.4f} exceeds threshold {args.max_live}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
