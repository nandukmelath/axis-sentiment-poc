"""The 2-hourly cycle: fetch new posts, then refresh engagement on recent ones.

This is what a scheduler should call. run_all.py is the full pipeline including
clustering and the exec brief; run_cycle is the lighter beat that keeps the
corpus and its engagement counts current.

    fetch.run_fetch      new posts
    fetch.refresh        re-poll engagement for posts inside the 24h window
    analyze.run_analyze  score whatever arrived
    analyze.run_categories  derived taxonomy (no tokens)

SCHEDULING (pick one)

  Windows Task Scheduler — every 2 hours:
    schtasks /create /tn "axis-cycle" /tr "python C:\\Users\\nandu\\axis-sentiment-poc\\run_cycle.py" /sc hourly /mo 2

  cron:
    0 */2 * * * cd /path/to/axis-sentiment-poc && python run_cycle.py

  Or leave it in the foreground:
    python run_cycle.py --loop

--loop is a convenience for a demo box, not a daemon: no supervision, no restart
on crash. Use the scheduler for anything that has to survive a reboot.
"""
import argparse
import subprocess
import sys
import time

INTERVAL_SECONDS = 2 * 60 * 60

STEPS = [
    # Explicit, first. On a fresh Postgres the dashboard container can win the race
    # to connect and query tables that no step has created yet; creating the schema
    # up front makes first boot deterministic instead of dependent on start order.
    ["-c", "import db; db.init_db()"],
    ["-m", "fetch.run_fetch"],
    ["-m", "fetch.refresh"],
    ["-m", "analyze.run_analyze", "--limit", "60"],
    ["-m", "analyze.run_categories"],
]


def cycle():
    for step in STEPS:
        print(f"\n=== {' '.join(step)} ===", flush=True)
        subprocess.run([sys.executable] + step, check=False)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--loop", action="store_true", help="run forever, every 2 hours")
    a = p.parse_args()
    while True:
        cycle()
        if not a.loop:
            break
        print(f"\ncycle complete — sleeping {INTERVAL_SECONDS // 3600}h", flush=True)
        time.sleep(INTERVAL_SECONDS)
