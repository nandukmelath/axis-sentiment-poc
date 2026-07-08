"""Build the GOLD layer (dialect-aware, runs on SQLite or Postgres).

Pipeline:
  ensure_tables()      create dims / facts / marts / CRM tables (idempotent)
  build_dim_author()   SCD Type 2 author dimension (history per @handle)
  build_facts()        fact_mention + fact_aspect_sentiment (customer voice only)
  resolution.build()   fact_interaction (bank-reply → resolved? satisfied?)  [separate module]
  build_marts()        mart_rm_enablement + mart_admin_analytics + mart_kpis

Run:  python -m warehouse.build
The prod path is dbt-core snapshots/tests on Supabase — see warehouse/README.md.
Same data, same grain; this hand-rolled version keeps the POC runnable on SQLite today.
"""
import json
import pandas as pd

import db
import config

# ---------------------------------------------------------------- column lists
DIM_AUTHOR_COLS = ["author_key", "author", "author_name", "influence_tier", "is_customer",
                   "customer_key", "typical_sentiment", "complaint_count",
                   "effective_from", "effective_to", "is_current", "version"]
DIM_CUSTOMER_COLS = ["customer_key", "customer_name", "segment", "rm_id", "city", "clv",
                     "risk_flag", "products_held", "updated_at"]
DIM_RM_COLS = ["rm_id", "rm_name", "branch", "region"]
DIM_PRODUCT_COLS = ["product_code", "product_name", "category"]
BRIDGE_COLS = ["author", "customer_key", "match_method", "confidence", "verified_by",
               "effective_from", "effective_to"]
FACT_MENTION_COLS = ["source_id", "author", "author_key", "customer_key", "source", "created_date",
                     "sentiment", "score", "emotion_intensity", "intent", "urgency", "recommended_team",
                     "rbi_category", "product", "engagement", "view_count", "confidence",
                     "churn_risk", "fraud_signal", "pii_present", "cluster_id", "date_key", "source_key"]
FACT_ASPECT_COLS = ["source_id", "aspect", "sentiment", "evidence"]
FACT_INTERACTION_COLS = ["issue_id", "conversation_id", "source", "customer_key", "author",
                         "inbound_source_id", "opened_at", "first_response_at", "response_latency_min",
                         "n_bank_replies", "resolved", "customer_satisfied", "resolution_type",
                         "inbound_score", "post_response_score", "recovery_delta", "updated_at"]
MART_RM_COLS = ["customer_key", "customer_name", "rm_id", "rm_name", "segment", "products_held",
                "current_sentiment", "sentiment_trend", "top_pain_point", "top_pain_area",
                "open_issues", "churn_flag", "fraud_flag", "cross_sell_product", "cross_sell_pitch",
                "last_interaction_outcome", "talking_point", "updated_at"]
MART_ADMIN_COLS = ["category", "team", "mentions", "pct_negative", "no_followup", "pending",
                   "in_progress", "resolved", "unresolved", "avg_response_latency_min", "updated_at"]
MART_KPI_COLS = ["id", "total_mentions", "pct_negative", "needs_followup", "resolved_count",
                 "sentiment_recovery_rate", "median_response_latency_min", "updated_at"]

DDL = [
    """CREATE TABLE IF NOT EXISTS dim_author (
        author_key TEXT PRIMARY KEY, author TEXT, author_name TEXT, influence_tier TEXT,
        is_customer INTEGER, customer_key TEXT, typical_sentiment TEXT, complaint_count INTEGER,
        effective_from TEXT, effective_to TEXT, is_current INTEGER, version INTEGER)""",
    """CREATE TABLE IF NOT EXISTS dim_customer (
        customer_key TEXT PRIMARY KEY, customer_name TEXT, segment TEXT, rm_id TEXT, city TEXT,
        clv REAL, risk_flag INTEGER, products_held TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS dim_rm (
        rm_id TEXT PRIMARY KEY, rm_name TEXT, branch TEXT, region TEXT)""",
    """CREATE TABLE IF NOT EXISTS dim_product (
        product_code TEXT PRIMARY KEY, product_name TEXT, category TEXT)""",
    """CREATE TABLE IF NOT EXISTS bridge_handle_customer (
        author TEXT PRIMARY KEY, customer_key TEXT, match_method TEXT, confidence REAL,
        verified_by TEXT, effective_from TEXT, effective_to TEXT)""",
    """CREATE TABLE IF NOT EXISTS fact_mention (
        source_id TEXT PRIMARY KEY, author TEXT, author_key TEXT, customer_key TEXT, source TEXT,
        created_date TEXT, sentiment TEXT, score REAL, emotion_intensity INTEGER, intent TEXT,
        urgency TEXT, recommended_team TEXT, rbi_category TEXT, product TEXT, engagement BIGINT,
        view_count BIGINT, confidence REAL, churn_risk INTEGER, fraud_signal INTEGER,
        pii_present INTEGER, cluster_id INTEGER)""",
    """CREATE TABLE IF NOT EXISTS fact_aspect_sentiment (
        source_id TEXT, aspect TEXT, sentiment TEXT, evidence TEXT)""",
    """CREATE TABLE IF NOT EXISTS fact_interaction (
        issue_id TEXT PRIMARY KEY, conversation_id TEXT, source TEXT, customer_key TEXT, author TEXT,
        inbound_source_id TEXT, opened_at TEXT, first_response_at TEXT, response_latency_min REAL,
        n_bank_replies INTEGER, resolved INTEGER, customer_satisfied INTEGER, resolution_type TEXT,
        inbound_score REAL, post_response_score REAL, recovery_delta REAL, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS mart_rm_enablement (
        customer_key TEXT PRIMARY KEY, customer_name TEXT, rm_id TEXT, rm_name TEXT, segment TEXT,
        products_held TEXT, current_sentiment TEXT, sentiment_trend TEXT, top_pain_point TEXT,
        top_pain_area TEXT, open_issues INTEGER, churn_flag INTEGER, fraud_flag INTEGER,
        cross_sell_product TEXT, cross_sell_pitch TEXT, last_interaction_outcome TEXT,
        talking_point TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS mart_admin_analytics (
        category TEXT, team TEXT, mentions INTEGER, pct_negative REAL, no_followup INTEGER,
        pending INTEGER, in_progress INTEGER, resolved INTEGER, unresolved INTEGER,
        avg_response_latency_min REAL, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS mart_kpis (
        id INTEGER PRIMARY KEY, total_mentions INTEGER, pct_negative REAL, needs_followup INTEGER,
        resolved_count INTEGER, sentiment_recovery_rate REAL, median_response_latency_min REAL,
        updated_at TEXT)""",
]

NEEDS_FOLLOWUP_INTENT = {"complaint", "churn_threat", "legal_threat", "fraud_report"}
AXIS = set(config.AXIS_HANDLES)

IDX = [
    "CREATE INDEX IF NOT EXISTS idx_fm_customer ON fact_mention(customer_key)",
    "CREATE INDEX IF NOT EXISTS idx_fm_author ON fact_mention(author_key)",
    "CREATE INDEX IF NOT EXISTS idx_fm_date ON fact_mention(created_date)",
    "CREATE INDEX IF NOT EXISTS idx_fi_inbound ON fact_interaction(inbound_source_id)",
    "CREATE INDEX IF NOT EXISTS idx_fi_customer ON fact_interaction(customer_key)",
    "CREATE INDEX IF NOT EXISTS idx_da_current ON dim_author(author, is_current)",
    "CREATE INDEX IF NOT EXISTS idx_bridge_customer ON bridge_handle_customer(customer_key)",
    "CREATE INDEX IF NOT EXISTS idx_fas_source ON fact_aspect_sentiment(source_id)",
]


def ensure_tables():
    db.init_db()          # silver tables + migrations + indexes + scored_posts view
    db.executescript(DDL + IDX)
    from warehouse import star
    star.ensure()         # conformed dims + fact_mention.date_key/source_key + star indexes


def _tier(reach):
    reach = reach or 0
    if reach >= 10000:
        return "macro"
    if reach >= 1000:
        return "mid"
    if reach >= 100:
        return "micro"
    return "nano"


def _norm(h):
    return (h or "").lstrip("@").lower()


# ------------------------------------------------------------ SCD Type 2 author
def build_dim_author():
    posts = db.df("""SELECT r.author, r.author_name, r.engagement, r.view_count, a.sentiment, a.intent
                     FROM raw_posts r LEFT JOIN analysis a ON r.source_id = a.source_id
                     WHERE r.author IS NOT NULL AND r.author <> ''""")
    if posts.empty:
        print("dim_author: no posts")
        return
    posts = posts[~posts["author"].map(_norm).isin(AXIS)]      # bank's own handles aren't authors
    bridge = db.df("SELECT author, customer_key FROM bridge_handle_customer")
    bmap = dict(zip(bridge["author"], bridge["customer_key"])) if not bridge.empty else {}

    desired = {}
    for author, g in posts.groupby("author"):
        reach = pd.concat([g["engagement"], g["view_count"]]).max()
        names = g["author_name"].dropna()
        sent = g["sentiment"].dropna()
        typical = sent.mode().iloc[0] if not sent.mode().empty else "neutral"
        ck = bmap.get(author)
        desired[author] = {
            "author": author,
            "author_name": names.iloc[-1] if not names.empty else author,
            "influence_tier": _tier(reach),
            "is_customer": 1 if ck else 0,
            "customer_key": ck,
            "typical_sentiment": typical,
            "complaint_count": int((g["intent"] == "complaint").sum()),
        }

    cur = db.df("SELECT * FROM dim_author WHERE is_current = 1")
    curmap = {r["author"]: r for _, r in cur.iterrows()} if not cur.empty else {}
    track = ["author_name", "influence_tier", "is_customer", "customer_key", "typical_sentiment", "complaint_count"]

    def _sigval(v):
        # Normalize so a real ABSENCE never looks like a change: DB NULL reads back as NaN
        # while the recomputed value is None ('nan' != 'None' minted a junk SCD2 version for
        # every non-customer author on every rebuild). Also fold 5.0 -> "5" (float64 columns).
        if v is None or (isinstance(v, float) and v != v):
            return ""
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v)

    def sig(d):
        return "|".join(_sigval(d.get(k)) for k in track)

    ts = db.now()
    new_v, changed = 0, 0
    for author, d in desired.items():
        old = curmap.get(author)
        if old is None:
            db.upsert_rows("dim_author", [{**d, "author_key": f"{author}#v1", "effective_from": ts,
                                           "effective_to": None, "is_current": 1, "version": 1}],
                           "author_key", DIM_AUTHOR_COLS)
            new_v += 1
        elif sig(old) != sig(d):
            db.execute("UPDATE dim_author SET is_current = 0, effective_to = :t WHERE author_key = :k",
                       {"t": ts, "k": old["author_key"]})
            v = int(old["version"]) + 1
            db.upsert_rows("dim_author", [{**d, "author_key": f"{author}#v{v}", "effective_from": ts,
                                           "effective_to": None, "is_current": 1, "version": v}],
                           "author_key", DIM_AUTHOR_COLS)
            changed += 1
    print(f"dim_author (SCD2): +{new_v} new handles, {changed} new versions")


# --------------------------------------------------------------------- facts
def build_facts():
    m = db.df("""SELECT r.source_id, r.author, r.source, r.created_at, r.engagement, r.view_count,
                        a.sentiment, a.score, a.emotion_intensity, a.intent, a.urgency, a.recommended_team,
                        a.rbi_category, a.product, a.confidence, a.churn_risk, a.fraud_signal,
                        a.pii_present, a.cluster_id, a.aspects_json
                 FROM raw_posts r JOIN analysis a ON r.source_id = a.source_id""")
    if m.empty:
        print("fact_mention: nothing to build")
        return
    m = m[~m["author"].map(_norm).isin(AXIS)].copy()           # customer voice only
    bridge = db.df("SELECT author, customer_key FROM bridge_handle_customer")
    bmap = dict(zip(bridge["author"], bridge["customer_key"])) if not bridge.empty else {}
    akey = db.df("SELECT author, author_key FROM dim_author WHERE is_current = 1")
    amap = dict(zip(akey["author"], akey["author_key"])) if not akey.empty else {}

    m["created_date"] = db.parse_dt(m["created_at"]).dt.strftime("%Y-%m-%d")
    m["author_key"] = m["author"].map(amap)
    m["customer_key"] = m["author"].map(bmap)
    # set the star FKs HERE so they land atomically with the fact row (replace_rows) — never a
    # window where fact_mention exists with NULL date_key (star.backfill remains an idempotent net).
    m["date_key"] = m["created_date"].map(lambda d: int(d.replace("-", "")) if isinstance(d, str) and d else None)
    m["source_key"] = m["source"]

    db.replace_rows("fact_mention", m.to_dict("records"), FACT_MENTION_COLS)   # atomic swap

    aspects = []
    for _, r in m.iterrows():
        try:
            for it in json.loads(r["aspects_json"] or "[]"):
                if isinstance(it, dict):
                    aspects.append({"source_id": r["source_id"], "aspect": it.get("aspect"),
                                    "sentiment": it.get("sentiment"), "evidence": (it.get("evidence") or "")[:300]})
        except Exception:
            pass
    db.replace_rows("fact_aspect_sentiment", aspects, FACT_ASPECT_COLS)
    print(f"fact_mention: {len(m)} rows · fact_aspect_sentiment: {len(aspects)} rows")


# --------------------------------------------------------------------- marts
def _sentiment_label(mean):
    if mean is None or pd.isna(mean):
        return "neutral"
    return "positive" if mean >= 0.15 else "negative" if mean <= -0.15 else "neutral"


def build_marts():
    from warehouse import xsell
    cust = db.df("SELECT * FROM dim_customer")
    rm = db.df("SELECT * FROM dim_rm")
    rmmap = {r["rm_id"]: r for _, r in rm.iterrows()} if not rm.empty else {}
    fi = db.df("SELECT * FROM fact_interaction")
    # customer-linked mentions with the detail we need for a pain point
    cm = db.df("""SELECT b.customer_key, a.score, a.sentiment, a.root_cause, a.summary, a.rbi_category,
                         a.recommended_team, a.intent, a.churn_risk, a.fraud_signal, r.url, r.created_at
                  FROM raw_posts r JOIN analysis a ON r.source_id = a.source_id
                  JOIN bridge_handle_customer b ON r.author = b.author""")

    # ---- RM enablement mart ----
    rm_rows = []
    ts = db.now()
    if not cust.empty:
        for _, c in cust.iterrows():
            ck = c["customer_key"]
            g = cm[cm["customer_key"] == ck].copy() if not cm.empty else cm
            held = {p.strip().lower() for p in (c["products_held"] or "").split(",") if p.strip()}
            cur_sent = _sentiment_label(g["score"].mean() if len(g) else None)
            trend = "flat"
            if len(g) >= 2:
                gg = g.dropna(subset=["created_at"]).sort_values("created_at")
                if len(gg) >= 2:
                    half = len(gg) // 2
                    older, recent = gg.iloc[:half]["score"].mean(), gg.iloc[half:]["score"].mean()
                    trend = "improving" if recent > older + 0.1 else "worsening" if recent < older - 0.1 else "flat"
            neg = g[g["sentiment"].isin(["negative", "mixed"])] if len(g) else g
            if len(neg):
                worst = neg.sort_values("score").iloc[0]
                pain = worst["root_cause"] or worst["summary"] or "(see mention)"
                pain_area = worst["rbi_category"] if worst["rbi_category"] not in (None, "not_applicable") \
                    else worst["recommended_team"]
                worst_cat, worst_team, worst_intent = worst["rbi_category"], worst["recommended_team"], worst["intent"]
            else:
                pain, pain_area = "No negative mentions on record", "none"
                worst_cat, worst_team, worst_intent = "not_applicable", "none", "other"
            xs_prod, xs_pitch = xsell.recommend(held, worst_cat, worst_team, worst_intent)
            fic = fi[fi["customer_key"] == ck] if not fi.empty else fi
            if len(fic):
                last = fic.sort_values("opened_at").iloc[-1]
                out = ("resolved · satisfied" if last["resolved"] and last["customer_satisfied"] == 1
                       else "resolved" if last["resolved"]
                       else "awaiting response" if pd.isna(last["first_response_at"])
                       else "in progress")
            else:
                out = "no public interaction"
            rmrow = rmmap.get(c["rm_id"], {})
            rm_rows.append({
                "customer_key": ck, "customer_name": c["customer_name"], "rm_id": c["rm_id"],
                "rm_name": rmrow.get("rm_name", ""), "segment": c["segment"], "products_held": c["products_held"],
                "current_sentiment": cur_sent, "sentiment_trend": trend, "top_pain_point": pain,
                "top_pain_area": pain_area, "open_issues": int(len(neg)),
                "churn_flag": int(g["churn_risk"].fillna(0).max()) if len(g) else 0,
                "fraud_flag": int(g["fraud_signal"].fillna(0).max()) if len(g) else 0,
                "cross_sell_product": xs_prod, "cross_sell_pitch": xs_pitch,
                "last_interaction_outcome": out,
                "talking_point": f"Lead with the {pain_area} issue ({pain}); once acknowledged, pitch {xs_prod}.",
                "updated_at": ts,
            })
    db.replace_rows("mart_rm_enablement", rm_rows, MART_RM_COLS)

    # ---- Admin analytics mart (follow-up bifurcation by category × team) ----
    fm = db.df("SELECT * FROM fact_mention")
    resolved_ids = set(fi[fi["resolved"] == 1]["inbound_source_id"]) if not fi.empty else set()
    responded_ids = set(fi[fi["first_response_at"].notna()]["inbound_source_id"]) if not fi.empty else set()
    any_ids = set(fi["inbound_source_id"]) if not fi.empty else set()

    def status(row):
        nf = (row["intent"] in NEEDS_FOLLOWUP_INTENT) or (row["urgency"] in ("high", "critical"))
        if not nf:
            return "no_followup"
        sid = row["source_id"]
        if sid in resolved_ids:
            return "resolved"
        if sid in responded_ids:
            return "in_progress"
        if sid in any_ids:
            return "unresolved"
        return "pending"

    admin_rows = []
    if not fm.empty:
        fm = fm.copy()
        fm["followup"] = fm.apply(status, axis=1)
        fm["category"] = fm["rbi_category"].fillna("not_applicable")
        fm["team"] = fm["recommended_team"].fillna("none")
        lat = fi.set_index("inbound_source_id")["response_latency_min"] if not fi.empty else pd.Series(dtype=float)
        for (cat, team), g in fm.groupby(["category", "team"]):
            counts = g["followup"].value_counts().to_dict()
            g_lat = lat.reindex(g["source_id"]).dropna()
            admin_rows.append({
                "category": cat, "team": team, "mentions": int(len(g)),
                "pct_negative": round(100 * g["sentiment"].isin(["negative", "mixed"]).mean(), 1),
                "no_followup": counts.get("no_followup", 0), "pending": counts.get("pending", 0),
                "in_progress": counts.get("in_progress", 0), "resolved": counts.get("resolved", 0),
                "unresolved": counts.get("unresolved", 0),
                "avg_response_latency_min": round(float(g_lat.mean()), 1) if len(g_lat) else None,
                "updated_at": ts,
            })
    db.replace_rows("mart_admin_analytics", admin_rows, MART_ADMIN_COLS)

    # ---- Headline KPIs incl. the north-star Sentiment Recovery Rate ----
    total = int(len(fm))
    pct_neg = round(100 * fm["sentiment"].isin(["negative", "mixed"]).mean(), 1) if total else 0.0
    needs_fu = int(sum(1 for _, r in fm.iterrows() if status(r) != "no_followup")) if total else 0
    resolved_ct = len(resolved_ids)
    recovery_rate, med_lat = 0.0, None
    if not fi.empty:
        responded = fi[fi["first_response_at"].notna()]
        if len(responded):
            recovered = responded[(responded["customer_satisfied"] == 1) | (responded["recovery_delta"] > 0)]
            recovery_rate = round(100 * len(recovered) / len(responded), 1)
            med_lat = round(float(responded["response_latency_min"].dropna().median()), 1) \
                if responded["response_latency_min"].notna().any() else None
    db.replace_rows("mart_kpis", [{
        "id": 1, "total_mentions": total, "pct_negative": pct_neg, "needs_followup": needs_fu,
        "resolved_count": resolved_ct, "sentiment_recovery_rate": recovery_rate,
        "median_response_latency_min": med_lat, "updated_at": ts,
    }], MART_KPI_COLS)
    print(f"marts: rm_enablement={len(rm_rows)} · admin_analytics={len(admin_rows)} · "
          f"recovery_rate={recovery_rate}%")


def main(step="all"):
    ensure_tables()
    if step in ("all", "dims"):
        build_dim_author()
    if step in ("all", "facts"):
        build_facts()
    if step in ("all", "resolution"):
        from warehouse import resolution
        resolution.build_interactions()
    if step in ("all", "marts"):
        build_marts()
    if step in ("all", "star"):
        from warehouse import star
        star.build_all()      # conformed dims + fact keys + fact_daily + mart_channel + views
    print(f"warehouse step '{step}' complete.")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", choices=["all", "dims", "facts", "resolution", "marts", "star"], default="all")
    a = ap.parse_args()
    main(a.step)
