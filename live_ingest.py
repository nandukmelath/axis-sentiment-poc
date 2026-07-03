"""Continuous ingest loop — makes the dashboard genuinely live.
Every INTERVAL seconds: fetch all sources -> analyze new -> re-cluster.
The dashboard (auto-refreshing) shows new mentions appear on their own.

Run in a second terminal alongside the dashboard:
    python live_ingest.py --interval 120
    python live_ingest.py --interval 120 --no-analyze     # fetch only (0 LLM cost)

Free-tier note: analyzing every cycle spends Gemini quota. Keep --analyze-limit small
or use --no-analyze and let a scheduled run_all.py do the scoring.
"""
import argparse, subprocess, sys, time, datetime

ap = argparse.ArgumentParser()
ap.add_argument("--interval", type=int, default=120, help="seconds between cycles")
ap.add_argument("--analyze-limit", type=int, default=12, help="max posts scored per cycle")
ap.add_argument("--no-analyze", action="store_true", help="fetch only, skip LLM")
args = ap.parse_args()


def run(step):
    subprocess.run([sys.executable] + step, check=False)


print(f"live ingest every {args.interval}s (Ctrl+C to stop)")
while True:
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"\n[{ts}] cycle")
    run(["-m", "fetch.run_fetch"])
    if not args.no_analyze:
        run(["-m", "analyze.run_analyze", "--limit", str(args.analyze_limit)])
        run(["-m", "analyze.embed_cluster"])
    print(f"[{ts}] sleeping {args.interval}s ...")
    time.sleep(args.interval)
