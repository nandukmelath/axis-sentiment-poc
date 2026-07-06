"""Tier-2 intelligence (all keyless): churn prediction, trend forecasting, entity (NER)
extraction. Pure/sklearn/numpy — no LLM required.

Run:  python -m analytics.intelligence
"""
import numpy as np
import pandas as pd

import db
from analytics.features import (ensure_tables, _base, CHURN_COLS, FORECAST_COLS, ENTITY_COLS,
                                 CITY_REGION)

# ------------------------------------------------------------------ churn
def churn_features(d):
    rows = []
    for author, g in d.groupby("author"):
        if not author:
            continue
        n = len(g)
        neg = int(g["sentiment"].isin(["negative", "mixed"]).sum())
        rows.append({"author": author, "mentions": n,
                     "complaints": int((g["intent"] == "complaint").sum()),
                     "churn_threat": int((g["intent"] == "churn_threat").sum()),
                     "avg_score": float(g["score"].mean()), "neg_ratio": neg / n,
                     "churn_flag": int(g["churn_risk"].fillna(0).max())})
    return pd.DataFrame(rows)


def build_churn(top=50):
    ensure_tables()
    d = _base()
    db.execute("DELETE FROM mart_churn_risk")
    if d.empty:
        return 0
    f = churn_features(d)
    if f.empty:
        return 0
    y = ((f["churn_flag"] == 1) | (f["churn_threat"] > 0)).astype(int)
    X = f[["mentions", "complaints", "avg_score", "neg_ratio"]].fillna(0).values
    probs = None
    if 2 <= int(y.sum()) < len(y):
        try:
            from sklearn.linear_model import LogisticRegression
            probs = LogisticRegression(max_iter=500, class_weight="balanced").fit(X, y).predict_proba(X)[:, 1]
        except Exception:
            probs = None
    if probs is None:                       # heuristic fallback (no trainable signal)
        probs = (0.5 * f["neg_ratio"] + 0.3 * (f["complaints"] > 0).astype(float)
                 + 0.2 * (f["churn_threat"] > 0).astype(float)).clip(0, 1).values
    bridge = db.df("""SELECT b.author, c.customer_name FROM bridge_handle_customer b
                      LEFT JOIN dim_customer c ON b.customer_key=c.customer_key""")
    namemap = dict(zip(bridge["author"], bridge["customer_name"])) if not bridge.empty else {}
    rows = []
    for (_, r), p in zip(f.iterrows(), probs):
        top_factor = ("churn threat stated" if r["churn_threat"] > 0
                      else "high negativity" if r["neg_ratio"] > 0.6
                      else "repeat complaints" if r["complaints"] > 1 else "monitor")
        rows.append({"entity_key": r["author"], "name": namemap.get(r["author"]) or r["author"],
                     "kind": "customer" if r["author"] in namemap else "handle",
                     "churn_prob": round(float(p), 3), "complaints": int(r["complaints"]),
                     "avg_score": round(float(r["avg_score"]), 3), "mentions": int(r["mentions"]),
                     "top_factor": top_factor, "updated_at": db.now()})
    rows = sorted(rows, key=lambda x: -x["churn_prob"])[:top]
    db.upsert_rows("mart_churn_risk", rows, "entity_key", CHURN_COLS)
    return len(rows)


# ------------------------------------------------------------------ forecast
def forecast_series(vals, horizon=3):
    """Linear-fit forecast of the next `horizon` points. Returns (preds, trend_label)."""
    vals = [float(v) for v in vals]
    if len(vals) < 2:
        return [max(vals[-1], 0.0) if vals else 0.0] * horizon, "flat"
    x = np.arange(len(vals))
    coef = np.polyfit(x, vals, 1)
    slope = coef[0]
    preds = [max(0.0, float(np.polyval(coef, len(vals) + h))) for h in range(horizon)]
    trend = "rising" if slope > 0.3 else "falling" if slope < -0.3 else "flat"
    return preds, trend


def build_forecast(horizon=3):
    ensure_tables()
    d = _base()
    db.execute("DELETE FROM mart_forecast")
    if d.empty:
        return 0
    d["day"] = pd.to_datetime(d["created_at"], errors="coerce", utc=True).dt.strftime("%Y-%m-%d")
    d = d.dropna(subset=["day"])
    d["category"] = d["rbi_category"].fillna("not_applicable")
    rows = []
    ts = db.now()
    for cat, g in d.groupby("category"):
        daily = g.groupby("day").size().sort_index()
        if len(daily) < 2:
            continue
        preds, trend = forecast_series(daily.tolist(), horizon)
        last = pd.to_datetime(daily.index[-1])
        for h, p in enumerate(preds, start=1):
            rows.append({"category": cat, "horizon_day": (last + pd.Timedelta(days=h)).strftime("%Y-%m-%d"),
                         "predicted_mentions": round(p, 1), "trend": trend, "updated_at": ts})
    db.insert_rows("mart_forecast", rows, FORECAST_COLS)
    return len(rows)


# ------------------------------------------------------------------ NER
AXIS_PRODUCTS = ["magnus", "axis ace", "ace card", "flipkart axis", "axis mobile", "asap",
                 "burgundy", "atlas", "axis reserve", "axis neo", "my zone", "privilege",
                 "vistara", "airtel axis", "axis direct", "axis max life"]


def extract_entities(text):
    low = (text or "").lower()
    ents = []
    for p in AXIS_PRODUCTS:
        if p in low:
            ents.append((p.title(), "product"))
    for kw, (city, _region) in CITY_REGION.items():
        if kw in low:
            ents.append((city, "city"))
    # de-dup within a post
    seen, out = set(), []
    for e in ents:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def build_entities():
    ensure_tables()
    d = _base()
    db.execute("DELETE FROM mart_entities")
    if d.empty:
        return 0
    recs = []
    for _, r in d.iterrows():
        for ent, etype in extract_entities(r["text"]):
            recs.append({"entity": ent, "etype": etype, "score": r["score"]})
    if not recs:
        return 0
    ef = pd.DataFrame(recs)
    rows = []
    ts = db.now()
    for (ent, etype), g in ef.groupby(["entity", "etype"]):
        rows.append({"entity": ent, "etype": etype, "mentions": int(len(g)),
                     "avg_score": round(float(g["score"].mean()), 3), "updated_at": ts})
    db.insert_rows("mart_entities", rows, ENTITY_COLS)
    return len(rows)


def build_all():
    import json
    n = {"churn": build_churn(), "forecast": build_forecast(), "entities": build_entities()}
    print("intelligence:", json.dumps(n))
    return n


if __name__ == "__main__":
    build_all()
