"""Proper star-schema layer — conformed dimensions + enriched fact keys + a daily aggregate
fact + a channel mart + denormalized analytical views. ADDITIVE: every existing table and the
live dashboard/marts keep working; this adds real time-intelligence and channel analytics on top.

What it adds (dialect-aware, SQLite + Postgres):
  dim_date        conformed calendar (day grain)         -> time intelligence
  dim_source      12 ingest channels typed by medium     -> channel / source-type analysis
  dim_team        10 routing teams + SLA + escalation     -> ops workload + SLA
  dim_category    8 RBI categories + regulatory + severity-> compliance slicing
  fact_mention.date_key / .source_key  (surrogate FKs)   -> real star joins
  fact_daily      date x source x sentiment aggregate     -> fast trends / forecast / SOV-over-time
  mart_channel    source_type roll-up                     -> "which channels carry the pain"
  vw_mention      fact + all dims + silver enrichment      -> one-stop analytical view (BI/API/ad-hoc)
  vw_daily_sentiment  fact_daily + dim_date + dim_source   -> clean time series
"""
import pandas as pd
import db

# ---- conformed dimension seeds (values verified against live data 2026-07-08) -----------
# source_key, source_name, source_type, medium, is_keyless, reliability
SOURCE_SEED = [
    ("news", "Google News RSS", "news", "aggregator", 1, "med"),
    ("rssnews", "Banking-desk RSS", "news", "publisher", 1, "high"),
    ("gdelt", "GDELT news index", "news", "aggregator", 1, "med"),
    ("technofino", "Technofino forum", "forum", "community", 1, "high"),
    ("reddit", "Reddit", "social", "community", 1, "high"),
    ("hackernews", "Hacker News", "forum", "community", 1, "low"),
    ("play", "Google Play reviews", "review", "appstore", 1, "high"),
    ("appstore", "Apple App Store", "review", "appstore", 1, "high"),
    ("twitter", "X / Twitter", "social", "microblog", 0, "high"),
    ("mastodon", "Mastodon", "social", "microblog", 1, "low"),
    ("bluesky", "Bluesky", "social", "microblog", 1, "low"),
    ("youtube", "YouTube", "social", "video", 0, "med"),
]
# team_code, team_name, owner_role, sla_hours, is_escalation
TEAM_SEED = [
    ("customer_support", "Customer Support", "Support Lead", 24, 0),
    ("app_engineering", "App Engineering", "Eng Manager", 48, 0),
    ("payments_upi", "Payments / UPI", "Payments Head", 8, 1),
    ("cards", "Cards", "Cards Product", 24, 0),
    ("loans", "Loans", "Lending Ops", 48, 0),
    ("fraud_cyber", "Fraud / Cyber", "Fraud Ops", 2, 1),
    ("branch_ops", "Branch Operations", "Branch Head", 48, 0),
    ("retention", "Retention", "Retention Lead", 12, 1),
    ("comms_pr", "Comms / PR", "PR Lead", 6, 1),
    ("none", "Unassigned", "Triage", 72, 0),
]
# category_code (RBI), category_name, is_regulatory, severity
CATEGORY_SEED = [
    ("upi", "UPI & payments", 1, "high"),
    ("credit_card", "Credit cards", 1, "high"),
    ("deposit_accounts", "Deposit accounts", 1, "med"),
    ("loans_advances", "Loans & advances", 1, "med"),
    ("levy_of_charges", "Levy of charges/fees", 1, "med"),
    ("mobile_internet_banking", "Mobile / internet banking", 1, "high"),
    ("other", "Other (RBI-relevant)", 1, "med"),
    ("not_applicable", "Not regulatory", 0, "low"),
]

DIM_SOURCE_COLS = ["source_key", "source_name", "source_type", "medium", "is_keyless", "reliability"]
DIM_TEAM_COLS = ["team_code", "team_name", "owner_role", "sla_hours", "is_escalation"]
DIM_CATEGORY_COLS = ["category_code", "category_name", "is_regulatory", "severity"]
DIM_DATE_COLS = ["date_key", "full_date", "year", "quarter", "month", "month_name",
                 "week", "day", "day_of_week", "day_name", "is_weekend"]
FACT_DAILY_COLS = ["date_key", "source_key", "sentiment", "mentions", "avg_score",
                   "negatives", "complaints", "fraud_ct", "churn_ct"]
MART_CHANNEL_COLS = ["source_type", "mentions", "pct_negative", "avg_score", "complaints",
                     "fraud_ct", "top_team", "updated_at"]

DDL = [
    """CREATE TABLE IF NOT EXISTS dim_date (
        date_key INTEGER PRIMARY KEY, full_date TEXT, year INTEGER, quarter INTEGER,
        month INTEGER, month_name TEXT, week INTEGER, day INTEGER, day_of_week INTEGER,
        day_name TEXT, is_weekend INTEGER)""",
    """CREATE TABLE IF NOT EXISTS dim_source (
        source_key TEXT PRIMARY KEY, source_name TEXT, source_type TEXT, medium TEXT,
        is_keyless INTEGER, reliability TEXT)""",
    """CREATE TABLE IF NOT EXISTS dim_team (
        team_code TEXT PRIMARY KEY, team_name TEXT, owner_role TEXT, sla_hours INTEGER,
        is_escalation INTEGER)""",
    """CREATE TABLE IF NOT EXISTS dim_category (
        category_code TEXT PRIMARY KEY, category_name TEXT, is_regulatory INTEGER, severity TEXT)""",
    """CREATE TABLE IF NOT EXISTS fact_daily (
        date_key INTEGER, source_key TEXT, sentiment TEXT, mentions INTEGER, avg_score REAL,
        negatives INTEGER, complaints INTEGER, fraud_ct INTEGER, churn_ct INTEGER)""",
    """CREATE TABLE IF NOT EXISTS mart_channel (
        source_type TEXT PRIMARY KEY, mentions INTEGER, pct_negative REAL, avg_score REAL,
        complaints INTEGER, fraud_ct INTEGER, top_team TEXT, updated_at TEXT)""",
]
IDX = [
    "CREATE INDEX IF NOT EXISTS idx_fm_source ON fact_mention(source)",
    "CREATE INDEX IF NOT EXISTS idx_fm_sentiment ON fact_mention(sentiment)",
    "CREATE INDEX IF NOT EXISTS idx_fm_team ON fact_mention(recommended_team)",
    "CREATE INDEX IF NOT EXISTS idx_fm_rbi ON fact_mention(rbi_category)",
    "CREATE INDEX IF NOT EXISTS idx_fm_cluster ON fact_mention(cluster_id)",
    "CREATE INDEX IF NOT EXISTS idx_fm_datekey ON fact_mention(date_key)",
    "CREATE INDEX IF NOT EXISTS idx_fm_sourcekey ON fact_mention(source_key)",
    "CREATE INDEX IF NOT EXISTS idx_fd_date ON fact_daily(date_key)",
    "CREATE INDEX IF NOT EXISTS idx_fd_source ON fact_daily(source_key)",
]


def _add_column(table, col, coltype):
    """ADD COLUMN only if missing — checking first avoids issuing a failing ALTER (which on
    Postgres would abort the surrounding transaction). Works on SQLite + Postgres."""
    if col not in db._existing_cols(table):
        db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")


def ensure():
    db.executescript(DDL)
    _add_column("fact_mention", "date_key", "INTEGER")
    _add_column("fact_mention", "source_key", "TEXT")
    db.executescript(IDX)


# ---- dimension builders --------------------------------------------------------
def build_static_dims():
    ts = db.now()
    db.upsert_rows("dim_source", [dict(zip(DIM_SOURCE_COLS, r)) for r in SOURCE_SEED],
                   "source_key", DIM_SOURCE_COLS)
    db.upsert_rows("dim_team", [dict(zip(DIM_TEAM_COLS, r)) for r in TEAM_SEED],
                   "team_code", DIM_TEAM_COLS)
    db.upsert_rows("dim_category", [dict(zip(DIM_CATEGORY_COLS, r)) for r in CATEGORY_SEED],
                   "category_code", DIM_CATEGORY_COLS)
    return len(SOURCE_SEED), len(TEAM_SEED), len(CATEGORY_SEED)


def build_dim_date():
    r = db.df("SELECT MIN(created_date) lo, MAX(created_date) hi FROM fact_mention "
              "WHERE created_date IS NOT NULL")
    if r.empty or pd.isna(r.iloc[0]["lo"]):
        print("dim_date: no dated facts yet")
        return 0
    rng = pd.date_range(r.iloc[0]["lo"], r.iloc[0]["hi"], freq="D", tz="UTC")
    rows = [{
        "date_key": int(d.strftime("%Y%m%d")), "full_date": d.strftime("%Y-%m-%d"),
        "year": d.year, "quarter": (d.month - 1) // 3 + 1, "month": d.month,
        "month_name": d.strftime("%b"), "week": int(d.strftime("%V")), "day": d.day,
        "day_of_week": d.dayofweek, "day_name": d.strftime("%a"),
        "is_weekend": 1 if d.dayofweek >= 5 else 0,
    } for d in rng]
    db.replace_rows("dim_date", rows, DIM_DATE_COLS)     # atomic — no empty-table gap for readers
    return len(rows)


def backfill_fact_keys():
    # date_key from the (now-correct) created_date; source_key = the ingest channel.
    db.execute("UPDATE fact_mention SET date_key = CAST(REPLACE(created_date,'-','') AS INTEGER) "
               "WHERE created_date IS NOT NULL")
    db.execute("UPDATE fact_mention SET source_key = source WHERE source IS NOT NULL")


def build_fact_daily():
    fm = db.df("""SELECT date_key, source_key, sentiment, score, intent, fraud_signal, churn_risk
                  FROM fact_mention WHERE date_key IS NOT NULL""")
    if fm.empty:
        db.execute("DELETE FROM fact_daily")
        print("fact_daily: no dated facts")
        return 0
    fm["neg"] = fm["sentiment"].isin(["negative", "mixed"]).astype(int)
    fm["cmp"] = (fm["intent"] == "complaint").astype(int)
    g = fm.groupby(["date_key", "source_key", "sentiment"], dropna=False).agg(
        mentions=("score", "size"), avg_score=("score", "mean"),
        negatives=("neg", "sum"), complaints=("cmp", "sum"),
        fraud_ct=("fraud_signal", "sum"), churn_ct=("churn_risk", "sum")).reset_index()
    g["avg_score"] = g["avg_score"].round(3)
    db.replace_rows("fact_daily", g.to_dict("records"), FACT_DAILY_COLS)   # atomic
    return len(g)


def build_mart_channel():
    d = db.df("""SELECT s.source_type, f.sentiment, f.score, f.intent, f.fraud_signal, f.recommended_team
                 FROM fact_mention f JOIN dim_source s ON f.source_key = s.source_key""")
    if d.empty:
        db.execute("DELETE FROM mart_channel")
        return 0
    ts = db.now()
    rows = []
    for st, g in d.groupby("source_type"):
        teams = g[g["recommended_team"] != "none"]["recommended_team"]
        rows.append({
            "source_type": st, "mentions": int(len(g)),
            "pct_negative": round(100 * g["sentiment"].isin(["negative", "mixed"]).mean(), 1),
            "avg_score": round(float(g["score"].mean()), 3),
            "complaints": int((g["intent"] == "complaint").sum()),
            "fraud_ct": int(g["fraud_signal"].fillna(0).sum()),
            "top_team": teams.mode().iloc[0] if not teams.mode().empty else "none",
            "updated_at": ts,
        })
    db.replace_rows("mart_channel", rows, MART_CHANNEL_COLS)   # atomic
    return len(rows)


# ---- denormalized analytical views --------------------------------------------
VIEWS = {
    "vw_mention": """
        SELECT f.source_id, f.created_date, d.year, d.month, d.month_name, d.week, d.day_name,
               d.is_weekend, f.source, s.source_type, s.medium, s.reliability,
               f.author, f.author_key, da.influence_tier, da.is_customer, f.customer_key,
               f.sentiment, f.score, a.emotion, a.emotion_intensity, a.sarcasm,
               f.intent, f.urgency, f.recommended_team, t.team_name, t.sla_hours, t.is_escalation,
               f.rbi_category, c.category_name, c.is_regulatory, c.severity,
               f.product, a.theme, a.root_cause, a.fraud_type, a.summary,
               f.engagement, f.view_count, f.confidence,
               f.churn_risk, f.fraud_signal, f.pii_present, f.cluster_id
        FROM fact_mention f
        LEFT JOIN analysis a   ON f.source_id = a.source_id
        LEFT JOIN dim_date d   ON f.date_key = d.date_key
        LEFT JOIN dim_source s ON f.source_key = s.source_key
        LEFT JOIN dim_author da ON f.author_key = da.author_key
        LEFT JOIN dim_team t   ON f.recommended_team = t.team_code
        LEFT JOIN dim_category c ON f.rbi_category = c.category_code""",
    "vw_daily_sentiment": """
        SELECT fd.date_key, d.full_date, d.year, d.month, d.week, d.day_name, d.is_weekend,
               fd.source_key, s.source_name, s.source_type, fd.sentiment,
               fd.mentions, fd.avg_score, fd.negatives, fd.complaints, fd.fraud_ct, fd.churn_ct
        FROM fact_daily fd
        LEFT JOIN dim_date d   ON fd.date_key = d.date_key
        LEFT JOIN dim_source s ON fd.source_key = s.source_key""",
}


def create_views():
    # DROP+CREATE per view in ONE transaction (db.executescript) so a concurrent reader never
    # hits the window where the view momentarily doesn't exist ('relation does not exist').
    for name, sql in VIEWS.items():
        db.executescript([f"DROP VIEW IF EXISTS {name}", f"CREATE VIEW {name} AS {sql}"])


def build_all():
    ensure()
    s, t, c = build_static_dims()
    backfill_fact_keys()
    nd = build_dim_date()
    fdaily = build_fact_daily()
    ch = build_mart_channel()
    create_views()
    print(f"star: dim_source={s} dim_team={t} dim_category={c} dim_date={nd} "
          f"fact_daily={fdaily} mart_channel={ch} views={len(VIEWS)}")


if __name__ == "__main__":
    build_all()
