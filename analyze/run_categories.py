"""Assign every scored post an issue_category (analyze/categories.py).

Idempotent and cheap — it is pure Python over rows already in the DB, no network
and no model. Safe to run on every pipeline pass; `--all` recomputes labels that
already exist, which is what you want after editing a rule.

Run:  python -m analyze.run_categories          # only unlabelled rows
      python -m analyze.run_categories --all    # recompute everything
"""
import argparse
import sys

import db
from analyze.categories import CATEGORY_LABEL, explain


def run(recompute=False):
    db.init_db()
    where = "" if recompute else "WHERE a.issue_category IS NULL OR a.issue_category = ''"
    rows = db.df(f"""SELECT a.source_id, a.intent, a.fraud_type, a.fraud_signal, a.aspects_json,
                            a.text_masked, r.text, r.source
                     FROM analysis a JOIN raw_posts r ON r.source_id = a.source_id {where}""")
    if rows.empty:
        print("categories: nothing to label")
        return 0

    updates = []
    for _, r in rows.iterrows():
        cat, why = explain(r)
        updates.append({"source_id": r["source_id"], "issue_category": cat, "category_reason": why})

    db.set_categories(updates)

    counts = {}
    for u in updates:
        counts[u["issue_category"]] = counts.get(u["issue_category"], 0) + 1
    print(f"categories: labelled {len(updates)} posts")
    for k, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5d}  {CATEGORY_LABEL.get(k, k)}")
    return len(updates)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--all", action="store_true", help="recompute existing labels too")
    a = p.parse_args()
    sys.exit(0 if run(recompute=a.all) >= 0 else 1)
