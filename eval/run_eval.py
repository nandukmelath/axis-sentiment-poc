"""Credibility harness: run the LLM layer on a hand-labeled gold set and measure
agreement with human labels. This is the number you show the bank.

Run:  python -m eval.run_eval     (or: python eval/run_eval.py)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
import pandas as pd
from sklearn.metrics import accuracy_score, classification_report
from analyze.gemini_client import analyze_batch

GOLD = Path(__file__).parent / "gold_set.csv"
FIELDS = ["sentiment", "urgency", "intent"]


def main():
    gold = pd.read_csv(GOLD)
    posts = [{"source_id": r.source_id, "text": r.text, "source": "gold"} for r in gold.itertuples()]

    print(f"running LLM on {len(posts)} gold posts ...")
    results = analyze_batch(posts)
    pred = {r.source_id: r for r in results}

    gmap = {r.source_id: r for r in gold.itertuples()}
    y = {f: ([], []) for f in FIELDS}          # field -> (true, pred)
    misses = []
    for sid, g in gmap.items():
        p = pred.get(sid)
        if not p:
            print(f"  WARN no prediction for {sid}")
            continue
        pv = {"sentiment": p.sentiment.value, "urgency": p.urgency.value, "intent": p.intent.value}
        for f in FIELDS:
            gv = str(getattr(g, f"gold_{f}"))
            y[f][0].append(gv)
            y[f][1].append(pv[f])
            if gv != pv[f]:
                misses.append((sid, f, gv, pv[f], g.text[:60]))

    print("\n=== AGREEMENT WITH HUMAN LABELS ===")
    for f in FIELDS:
        acc = accuracy_score(y[f][0], y[f][1])
        print(f"\n{f.upper()} accuracy: {acc:.0%}")
        print(classification_report(y[f][0], y[f][1], zero_division=0))

    if misses:
        print("=== DISAGREEMENTS (review these) ===")
        for sid, f, gv, pv, txt in misses:
            print(f"  {sid} {f}: gold={gv} pred={pv}  | {txt}")


if __name__ == "__main__":
    main()
