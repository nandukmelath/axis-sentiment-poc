"""Backfill: normalise created_at on rows written before db.norm_dt existed.

366 rows were stored in their source's own format (RFC-822 from RSS, mostly), so
every SQL-level date expression compared them as text. 62 ended up with a NULL
date_key and were invisible to the volume chart, the 30-day metrics, trend
detection and spike alerts.

Idempotent — rows already ISO are left alone. Run once before migrating to
Postgres, or the corruption travels with the data.

Run:  python -m tools.fix_dates --dry-run
      python -m tools.fix_dates
"""
import argparse

import db


def run(dry_run=False):
    db.init_db()
    rows = db.df("""SELECT source_id, source, created_at FROM raw_posts
                    WHERE created_at IS NOT NULL AND created_at NOT LIKE '20__-%'""")
    if rows.empty:
        print("dates: nothing to fix — every created_at is already ISO")
        return 0

    fixed, unparseable = [], []
    for _, r in rows.iterrows():
        iso = db.norm_dt(r["created_at"])
        if iso:
            fixed.append({"source_id": r["source_id"], "created_at": iso})
        else:
            unparseable.append((r["source"], r["created_at"]))

    by_source = {}
    for _, r in rows.iterrows():
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
    print(f"dates: {len(rows)} non-ISO rows across {len(by_source)} sources")
    for s, n in sorted(by_source.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5d}  {s}")

    if unparseable:
        print(f"\n{len(unparseable)} could not be parsed and are left untouched:")
        for s, v in unparseable[:5]:
            print(f"  [{s}] {v!r}")

    if dry_run:
        print(f"\ndry run — would rewrite {len(fixed)}")
        return len(fixed)

    if fixed:
        db.set_created_at(fixed)
    print(f"\nrewrote {len(fixed)} created_at values to ISO8601 UTC")
    return len(fixed)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    run(dry_run=a.dry_run)
