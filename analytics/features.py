"""Analytics marts for the product features (all keyless — pure aggregation over
existing silver/gold tables):

  mart_product_scorecard   per product: sentiment / complaints / NPS-proxy   (Product scorecards)
  mart_influencers         high-reach authors + their stance                  (Influencer watch)
  mart_team_queue          per owning team: open / critical worklist           (Auto-routing)
  mart_fraud               fraud clusters by type + sample handles/links       (Fraud board)
  mart_trends              per RBI-category daily volume + z-score anomaly      (Trend/anomaly)
  mart_geo                 sentiment by inferred city/region                    (Geo heatmap)

Run:  python -m analytics.features
"""
import json
import pandas as pd

import db
import config
from warehouse.build import ensure_tables as _gold_tables, _norm

AXIS = set(config.AXIS_HANDLES)

DDL = [
    """CREATE TABLE IF NOT EXISTS mart_product_scorecard (
        product TEXT PRIMARY KEY, mentions INTEGER, pct_negative REAL, complaints INTEGER,
        avg_score REAL, nps_proxy REAL, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS mart_influencers (
        author TEXT PRIMARY KEY, author_name TEXT, reach BIGINT, mentions INTEGER,
        avg_score REAL, stance TEXT, worst_summary TEXT, url TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS mart_team_queue (
        team TEXT PRIMARY KEY, open_items INTEGER, critical INTEGER, fraud INTEGER,
        avg_score REAL, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS mart_fraud (
        fraud_type TEXT PRIMARY KEY, cnt INTEGER, sample_handles TEXT, sample_url TEXT,
        avg_score REAL, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS mart_trends (
        day TEXT, category TEXT, mentions INTEGER, avg_score REAL, z_score REAL,
        anomaly INTEGER, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS mart_geo (
        city TEXT PRIMARY KEY, region TEXT, mentions INTEGER, pct_negative REAL,
        avg_score REAL, updated_at TEXT)""",
    # tables written by the active modules (created here so everything exists up-front)
    """CREATE TABLE IF NOT EXISTS competitor_posts (
        source_id TEXT PRIMARY KEY, brand TEXT, source TEXT, author TEXT, text TEXT, url TEXT,
        created_at TEXT, sentiment TEXT, score REAL, fetched_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS mart_competitor_sov (
        brand TEXT PRIMARY KEY, mentions INTEGER, pct_negative REAL, avg_score REAL,
        share_of_voice REAL, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS reply_drafts (
        source_id TEXT PRIMARY KEY, draft TEXT, model TEXT, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS alerts (
        alert_id TEXT PRIMARY KEY, kind TEXT, severity TEXT, title TEXT, detail TEXT,
        ref_url TEXT, created_at TEXT, sent INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS audit_log (
        audit_id TEXT PRIMARY KEY, ts TEXT, actor TEXT, action TEXT, detail TEXT)""",
    # --- Tier 2 (intelligence) ---
    """CREATE TABLE IF NOT EXISTS mart_churn_risk (
        entity_key TEXT PRIMARY KEY, name TEXT, kind TEXT, churn_prob REAL, complaints INTEGER,
        avg_score REAL, mentions INTEGER, top_factor TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS mart_forecast (
        category TEXT, horizon_day TEXT, predicted_mentions REAL, trend TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS mart_entities (
        entity TEXT, etype TEXT, mentions INTEGER, avg_score REAL, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS translations (
        source_id TEXT PRIMARY KEY, lang TEXT, english TEXT, model TEXT, created_at TEXT)""",
    # --- Tier 4 (trust / ops) ---
    """CREATE TABLE IF NOT EXISTS eval_history (
        run_ts TEXT, metric TEXT, value REAL)""",
    """CREATE TABLE IF NOT EXISTS run_metrics (
        run_ts TEXT PRIMARY KEY, mentions INTEGER, llm_calls INTEGER, tokens_est INTEGER,
        cost_usd_est REAL, provider TEXT)""",
]

CHURN_COLS = ["entity_key", "name", "kind", "churn_prob", "complaints", "avg_score", "mentions", "top_factor", "updated_at"]
FORECAST_COLS = ["category", "horizon_day", "predicted_mentions", "trend", "updated_at"]
ENTITY_COLS = ["entity", "etype", "mentions", "avg_score", "updated_at"]
TRANS_COLS = ["source_id", "lang", "english", "model", "created_at"]
EVAL_COLS = ["run_ts", "metric", "value"]
RUNM_COLS = ["run_ts", "mentions", "llm_calls", "tokens_est", "cost_usd_est", "provider"]

PRODUCT_COLS = ["product", "mentions", "pct_negative", "complaints", "avg_score", "nps_proxy", "updated_at"]
INFL_COLS = ["author", "author_name", "reach", "mentions", "avg_score", "stance", "worst_summary", "url", "updated_at"]
TEAM_COLS = ["team", "open_items", "critical", "fraud", "avg_score", "updated_at"]
FRAUD_COLS = ["fraud_type", "cnt", "sample_handles", "sample_url", "avg_score", "updated_at"]
TREND_COLS = ["day", "category", "mentions", "avg_score", "z_score", "anomaly", "updated_at"]
GEO_COLS = ["city", "region", "mentions", "pct_negative", "avg_score", "updated_at"]

NEEDS_FOLLOWUP = {"complaint", "churn_threat", "legal_threat", "fraud_report"}

# --- geo: keyword -> (canonical city, region) ---
CITY_REGION = {
    "mumbai": ("Mumbai", "West"), "bombay": ("Mumbai", "West"), "delhi": ("Delhi", "North"),
    "new delhi": ("Delhi", "North"), "bengaluru": ("Bengaluru", "South"), "bangalore": ("Bengaluru", "South"),
    "hyderabad": ("Hyderabad", "South"), "chennai": ("Chennai", "South"), "kolkata": ("Kolkata", "East"),
    "pune": ("Pune", "West"), "ahmedabad": ("Ahmedabad", "West"), "jaipur": ("Jaipur", "North"),
    "lucknow": ("Lucknow", "North"), "kochi": ("Kochi", "South"), "cochin": ("Kochi", "South"),
    "chandigarh": ("Chandigarh", "North"), "gurgaon": ("Gurugram", "North"), "gurugram": ("Gurugram", "North"),
    "noida": ("Noida", "North"), "surat": ("Surat", "West"), "indore": ("Indore", "Central"),
    "bhopal": ("Bhopal", "Central"), "nagpur": ("Nagpur", "West"), "patna": ("Patna", "East"),
    "kanpur": ("Kanpur", "North"), "coimbatore": ("Coimbatore", "South"), "vizag": ("Visakhapatnam", "South"),
    "visakhapatnam": ("Visakhapatnam", "South"), "thiruvananthapuram": ("Thiruvananthapuram", "South"),
    "trivandrum": ("Thiruvananthapuram", "South"), "guwahati": ("Guwahati", "East"),
}


def infer_city(text):
    """Return (city, region) if a known Indian city is mentioned, else (None, None)."""
    low = (text or "").lower()
    for kw, (city, region) in CITY_REGION.items():
        if kw in low:
            return city, region
    return None, None


def ensure_tables():
    _gold_tables()
    db.executescript(DDL)


# ------------------------------------------------------------------ builders
def _base():
    """analysis joined to raw_posts, customer voice only."""
    d = db.df("""SELECT a.source_id, a.sentiment, a.score, a.intent, a.urgency, a.recommended_team,
                        a.rbi_category, a.product, a.fraud_signal, a.fraud_type, a.churn_risk,
                        a.summary, r.author, r.author_name, r.text, r.url, r.created_at,
                        r.engagement, r.view_count
                 FROM analysis a JOIN raw_posts r ON a.source_id = r.source_id""")
    if not d.empty:
        d = d[~d["author"].fillna("").map(_norm).isin(AXIS)].copy()
    return d


def build_product_scorecard():
    d = _base()
    rows = []
    ts = db.now()
    if not d.empty:
        d = d[d["product"].fillna("unspecified").str.lower() != "unspecified"]
        for prod, g in d.groupby("product"):
            n = len(g)
            pos = (g["sentiment"] == "positive").sum()
            neg = g["sentiment"].isin(["negative", "mixed"]).sum()
            rows.append({"product": prod, "mentions": int(n),
                         "pct_negative": round(100 * neg / n, 1),
                         "complaints": int((g["intent"] == "complaint").sum()),
                         "avg_score": round(float(g["score"].mean()), 3),
                         "nps_proxy": round(100 * (pos - neg) / n, 1), "updated_at": ts})
    db.execute("DELETE FROM mart_product_scorecard")
    db.upsert_rows("mart_product_scorecard", rows, "product", PRODUCT_COLS)
    return len(rows)


def build_influencers(top=25):
    d = _base()
    rows = []
    ts = db.now()
    if not d.empty:
        d["reach"] = d[["engagement", "view_count"]].fillna(0).max(axis=1)
        for author, g in d.groupby("author"):
            if not author:
                continue
            reach = int(g["reach"].max())
            avg = float(g["score"].mean())
            worst = g.sort_values("score").iloc[0]
            stance = "positive" if avg >= 0.15 else "negative" if avg <= -0.15 else "neutral"
            rows.append({"author": author, "author_name": g["author_name"].dropna().iloc[-1] if g["author_name"].notna().any() else author,
                         "reach": reach, "mentions": int(len(g)), "avg_score": round(avg, 3),
                         "stance": stance, "worst_summary": str(worst["summary"] or "")[:160],
                         "url": worst["url"], "updated_at": ts})
        rows = sorted(rows, key=lambda r: -r["reach"])[:top]
    db.execute("DELETE FROM mart_influencers")
    db.upsert_rows("mart_influencers", rows, "author", INFL_COLS)
    return len(rows)


def build_team_queue():
    d = _base()
    rows = []
    ts = db.now()
    if not d.empty:
        d["nf"] = d.apply(lambda r: (r["intent"] in NEEDS_FOLLOWUP) or (r["urgency"] in ("high", "critical")), axis=1)
        for team, g in d.groupby(d["recommended_team"].fillna("none")):
            rows.append({"team": team, "open_items": int(g["nf"].sum()),
                         "critical": int((g["urgency"] == "critical").sum()),
                         "fraud": int(g["fraud_signal"].fillna(0).sum()),
                         "avg_score": round(float(g["score"].mean()), 3), "updated_at": ts})
    db.execute("DELETE FROM mart_team_queue")
    db.upsert_rows("mart_team_queue", rows, "team", TEAM_COLS)
    return len(rows)


def build_fraud_board():
    d = _base()
    rows = []
    ts = db.now()
    if not d.empty:
        f = d[d["fraud_signal"].fillna(0) == 1]
        for ftype, g in f.groupby(f["fraud_type"].fillna("suspected")):
            handles = [h for h in g["author"].dropna().unique().tolist() if h][:5]
            rows.append({"fraud_type": ftype, "cnt": int(len(g)),
                         "sample_handles": ", ".join(handles),
                         "sample_url": g["url"].dropna().iloc[0] if g["url"].notna().any() else "",
                         "avg_score": round(float(g["score"].mean()), 3), "updated_at": ts})
    db.execute("DELETE FROM mart_fraud")
    db.upsert_rows("mart_fraud", rows, "fraud_type", FRAUD_COLS)
    return len(rows)


def _zscores(series):
    """z-score of each value vs the series' own mean/std (population)."""
    s = pd.Series(series, dtype=float)
    mu, sd = s.mean(), s.std(ddof=0)
    if sd == 0 or pd.isna(sd):
        return [0.0] * len(s)
    return list(((s - mu) / sd).round(2))


def build_trends():
    d = _base()
    db.execute("DELETE FROM mart_trends")
    if d.empty:
        return 0
    d["day"] = db.parse_dt(d["created_at"]).dt.strftime("%Y-%m-%d")
    d = d.dropna(subset=["day"])
    d["category"] = d["rbi_category"].fillna("not_applicable")
    rows = []
    ts = db.now()
    for cat, g in d.groupby("category"):
        daily = g.groupby("day").agg(mentions=("source_id", "size"), avg_score=("score", "mean")).reset_index()
        daily = daily.sort_values("day")
        zs = _zscores(daily["mentions"].tolist())
        for (_, r), z in zip(daily.iterrows(), zs):
            rows.append({"day": r["day"], "category": cat, "mentions": int(r["mentions"]),
                         "avg_score": round(float(r["avg_score"]), 3), "z_score": float(z),
                         "anomaly": 1 if (z >= 2 and float(r["avg_score"]) < 0) else 0, "updated_at": ts})
    db.insert_rows("mart_trends", rows, TREND_COLS)
    return len(rows)


def build_geo():
    d = _base()
    db.execute("DELETE FROM mart_geo")
    if d.empty:
        return 0
    geo = d.assign(**{"cr": d["text"].map(infer_city)})
    geo["city"] = geo["cr"].map(lambda x: x[0])
    geo["region"] = geo["cr"].map(lambda x: x[1])
    geo = geo.dropna(subset=["city"])
    rows = []
    ts = db.now()
    for (city, region), g in geo.groupby(["city", "region"]):
        n = len(g)
        rows.append({"city": city, "region": region, "mentions": int(n),
                     "pct_negative": round(100 * g["sentiment"].isin(["negative", "mixed"]).mean(), 1),
                     "avg_score": round(float(g["score"].mean()), 3), "updated_at": ts})
    db.upsert_rows("mart_geo", rows, "city", GEO_COLS)
    return len(rows)


def build_competitor_sov():
    """Share-of-voice across brands from competitor_posts (+ Axis from analysis)."""
    ts = db.now()
    cp = db.df("SELECT brand, sentiment, score FROM competitor_posts")
    axis_n = int(db.df("SELECT COUNT(*) n FROM analysis").iloc[0]["n"])
    axis_neg = db.df("SELECT sentiment FROM analysis")
    frames = []
    if axis_n:
        frames.append({"brand": config.BRAND, "mentions": axis_n,
                       "pct_negative": round(100 * axis_neg["sentiment"].isin(["negative", "mixed"]).mean(), 1),
                       "avg_score": round(float(db.df("SELECT AVG(score) s FROM analysis").iloc[0]["s"] or 0), 3)})
    if not cp.empty:
        for brand, g in cp.groupby("brand"):
            frames.append({"brand": brand, "mentions": int(len(g)),
                           "pct_negative": round(100 * g["sentiment"].isin(["negative", "mixed"]).mean(), 1),
                           "avg_score": round(float(g["score"].mean()), 3)})
    total = sum(f["mentions"] for f in frames) or 1
    rows = [{**f, "share_of_voice": round(100 * f["mentions"] / total, 1), "updated_at": ts} for f in frames]
    db.execute("DELETE FROM mart_competitor_sov")
    db.upsert_rows("mart_competitor_sov", rows,
                   "brand", ["brand", "mentions", "pct_negative", "avg_score", "share_of_voice", "updated_at"])
    return len(rows)


def build_all():
    ensure_tables()
    n = {
        "product_scorecard": build_product_scorecard(),
        "influencers": build_influencers(),
        "team_queue": build_team_queue(),
        "fraud_board": build_fraud_board(),
        "trends": build_trends(),
        "geo": build_geo(),
        "competitor_sov": build_competitor_sov(),
    }
    print("analytics marts:", json.dumps(n))
    return n


if __name__ == "__main__":
    build_all()
