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
import time
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
    ap.add_argument("--collect-delay", type=int, default=15,
                    help="Seconds to wait after chaos starts before capturing Locust stats.")
    ap.add_argument("--collect-window", type=int,
                    help="Window duration passed to collect_live.py (defaults to --window).")
    ap.add_argument("--min-total", type=int, default=200,
                    help="Minimum Locust total requests required for a valid attempt.")
    ap.add_argument("--max-attempts", type=int, default=3,
                    help="Retry chaos+measurement if traffic stays below min-total.")
    ap.add_argument("--retry-sleep", type=int, default=5,
                    help="Seconds to sleep between attempts when re-trying.")
    args = ap.parse_args()

    log_path = Path(args.log)
    live_path = Path(args.live)
    summary_path = Path(args.summary)

    for path in (log_path, live_path, summary_path):
        if path.exists():
            path.unlink()

    def run_attempt(attempt: int):
        before = len(read_json_lines(log_path))
        chaos_cmd = [
            "bash",
            "scripts/compose_chaos.sh",
            str(args.p_fail),
            args.allowlist,
            str(args.window),
            str(log_path),
        ]
        collect_window = args.collect_window or args.window
        collect_cmd = [
            "python3",
            "scripts/collect_live.py",
            "--locust",
            args.locust,
            "--window",
            str(collect_window),
            "--out",
            str(live_path),
        ]

        print(f"[validation] attempt {attempt}: chaos window={args.window}s delay={args.collect_delay}s collect_window={collect_window}s", file=sys.stderr)
        chaos_proc = subprocess.Popen(chaos_cmd)
        try:
            if args.collect_delay > 0:
                time.sleep(args.collect_delay)
            subprocess.run(collect_cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"[validation] collect_live.py failed (attempt {attempt}): {e}", file=sys.stderr)
            chaos_proc.wait()
            return None
        finally:
            chaos_rc = chaos_proc.wait()
            if chaos_rc != 0:
                print(f"compose_chaos.sh exited with {chaos_rc}", file=sys.stderr)

        entries = read_json_lines(log_path)
        new_entries = entries[before:]
        last = new_entries[-1] if new_entries else (entries[-1] if entries else {})
        if not last:
            print(f"No chaos entries recorded in {log_path}", file=sys.stderr)
            return None

        with live_path.open() as fh:
            live = json.load(fh)
        r_live = float(live.get("R_live") or 0.0)

        summary = {
            "attempt": attempt,
            "window_s": args.window,
            "collect_window_s": collect_window,
            "collect_delay_s": args.collect_delay,
            "p_fail": args.p_fail,
            "min_kills": args.min_kills,
            "max_live": args.max_live,
            "min_total": args.min_total,
            "eligible": int(last.get("eligible") or 0),
            "killed": int(last.get("killed") or 0),
            "services": last.get("services"),
            "R_live": r_live,
            "detail": live.get("detail"),
        }
        return summary

    final_summary = None
    for attempt in range(1, args.max_attempts + 1):
        summary = run_attempt(attempt)
        if not summary:
            if attempt >= args.max_attempts:
                print("[validation] giving up after repeated collect failures", file=sys.stderr)
                return 1
            time.sleep(max(0, args.retry_sleep))
            continue
        killed = summary["killed"]
        eligible = summary["eligible"]
        detail = summary.get("detail") or {}
        total = int(detail.get("total") or 0)
        r_live = summary["R_live"]

        if eligible <= 0:
            print("Validation failed: no eligible services detected for chaos", file=sys.stderr)
            return 1
        if killed < args.min_kills:
            print(
                f"Validation failed: expected at least {args.min_kills} kills, got {killed}",
                file=sys.stderr,
            )
            return 1
        if total < args.min_total:
            if attempt >= args.max_attempts:
                print(
                    f"Validation failed: Locust total {total} < min_total {args.min_total} after {attempt} attempts",
                    file=sys.stderr,
                )
                final_summary = summary
                break
            print(
                f"[validation] attempt {attempt}: total={total} < min_total={args.min_total}, retrying...",
                file=sys.stderr,
            )
            time.sleep(max(0, args.retry_sleep))
            continue
        if r_live > args.max_live:
            if attempt >= args.max_attempts:
                print(
                    f"Validation failed: R_live={r_live:.4f} exceeds threshold {args.max_live} after {attempt} attempts",
                    file=sys.stderr,
                )
                final_summary = summary
                break
            print(
                f"[validation] attempt {attempt}: R_live={r_live:.4f} > max_live={args.max_live}, retrying...",
                file=sys.stderr,
            )
            time.sleep(max(0, args.retry_sleep))
            continue
        final_summary = summary
        break

    if not final_summary:
        print("Validation failed: no successful attempts recorded", file=sys.stderr)
        return 1

    with summary_path.open("w") as fh:
        json.dump(final_summary, fh)
    print(json.dumps(final_summary))

    detail = final_summary.get("detail") or {}
    total = int(detail.get("total") or 0)
    if total < args.min_total:
        return 1
    if final_summary["R_live"] > args.max_live:
        return 1
    if final_summary["killed"] < args.min_kills or final_summary["eligible"] <= 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
