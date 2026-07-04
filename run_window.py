"""One-click windowed run for the dashboard RUN button:
fetch Axis mentions for a time window -> transform -> VADER baseline -> warehouse -> marts.
Keyless + fast (no LLM depth). Each step is resilient so one failure doesn't abort the run.

Run:  python -m run_window [--window 1h|1d|1m]
"""
import argparse
import json


def run(window=None):
    summary = {"window": window or "all"}

    try:
        from fetch.run_fetch import run as fetch_run
        summary["fetched"] = fetch_run(window=window)
    except Exception as e:
        summary["fetch_error"] = str(e)[:150]

    try:
        from transform.beam_transform import run as transform_run
        summary["transformed"] = transform_run()
    except Exception as e:
        summary["transform_error"] = str(e)[:150]

    try:
        from analyze.run_analyze import run_baseline
        from db import init_db
        init_db()
        summary["scored"] = run_baseline()
    except Exception as e:
        summary["analyze_error"] = str(e)[:150]

    try:
        from warehouse import build
        build.main("all")
        summary["warehouse"] = "ok"
    except Exception as e:
        summary["warehouse_error"] = str(e)[:150]

    try:
        from analytics import features, actions
        features.build_all()
        actions.build_alerts()
        actions.weekly_digest()
        summary["features"] = "ok"
    except Exception as e:
        summary["features_error"] = str(e)[:150]

    print("RUN_WINDOW " + json.dumps(summary))
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", choices=["1h", "1d", "1m"], default=None)
    a = ap.parse_args()
    run(a.window)


if __name__ == "__main__":
    main()
