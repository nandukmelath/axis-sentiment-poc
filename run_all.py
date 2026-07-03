"""End-to-end: fetch -> analyze -> cluster -> exec brief.
Schedule this (Windows Task Scheduler) every N minutes for near-real-time.

Run:  python run_all.py
"""
import sys, subprocess

STEPS = [
    ["-m", "fetch.run_fetch"],
    ["-m", "analyze.run_analyze", "--limit", "60"],   # free-tier guard
    ["-m", "analyze.embed_cluster"],
    ["-m", "analyze.exec_summary"],
]

for step in STEPS:
    print(f"\n=== {' '.join(step)} ===")
    subprocess.run([sys.executable] + step, check=False)
