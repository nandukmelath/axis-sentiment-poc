"""Gold layer — SCD Type 2 history, resolution/CX fact, RM mart, cross-sell rules."""
import db
from warehouse import build, resolution, xsell


def _post(sid, author, text, created_at, conv=None, author_name=None):
    return dict(source_id=sid, source="twitter", author=author, author_name=author_name,
                text=text, url=f"http://x/{sid}", created_at=created_at, engagement=10,
                conversation_id=conv, lang="en")


def _analysis(sid, sentiment="negative", intent="complaint", score=-0.5, team="payments_upi",
              rbi="upi", root_cause="upi failed"):
    return dict(source_id=sid, sentiment=sentiment, score=score, emotion="frustration",
                emotion_intensity=4, sarcasm=0, intent=intent, urgency="high",
                urgency_reason="money stuck", product="Axis Mobile", root_cause=root_cause,
                rbi_category=rbi, recommended_team=team, recommended_action="fix",
                churn_risk=0, fraud_signal=0, fraud_type="none", pii_present=0,
                theme="upi", summary="upi failed", confidence=0.9, aspects_json="[]",
                cluster_id=None, model="groq", text_masked=text_for(sid), pii_types="")


def text_for(sid):
    return f"clean text {sid}"


# ---------------------------------------------------------------- SCD Type 2
def test_scd2_new_handle_then_versioned(fresh_db):
    db.upsert_posts([_post("r1", "@ravi", "upi failed money debited", "2026-07-01T10:00:00Z")])
    db.upsert_analysis(_analysis("r1"))
    build.build_dim_author()
    cur = db.df("SELECT * FROM dim_author WHERE author='@ravi'")
    assert len(cur) == 1 and int(cur.iloc[0]["version"]) == 1 and int(cur.iloc[0]["is_current"]) == 1

    # change a tracked attribute (display name) -> new SCD2 version
    db.execute("UPDATE raw_posts SET author_name='Ravi Verified' WHERE author='@ravi'")
    build.build_dim_author()
    rows = db.df("SELECT version, is_current, effective_to FROM dim_author WHERE author='@ravi' ORDER BY version")
    assert list(rows["version"]) == [1, 2]
    assert int(rows.iloc[0]["is_current"]) == 0 and rows.iloc[0]["effective_to"] is not None
    assert int(rows.iloc[1]["is_current"]) == 1


def test_scd2_idempotent_no_dup_version(fresh_db):
    db.upsert_posts([_post("r1", "@ravi", "upi failed", "2026-07-01T10:00:00Z")])
    db.upsert_analysis(_analysis("r1"))
    build.build_dim_author()
    build.build_dim_author()          # no change -> still 1 version
    assert len(db.df("SELECT * FROM dim_author WHERE author='@ravi'")) == 1


# ---------------------------------------------------------------- resolution / CX
def test_resolution_resolved_and_satisfied(fresh_db):
    conv = "thread:1"
    db.upsert_posts([
        _post("t1", "@ravi", "@AxisBank upi failed money debited fix now", "2026-07-01T10:00:00Z", conv),
        _post("t2", "@AxisBankSupport", "sorry Ravi, refund raised, please DM", "2026-07-01T11:00:00Z", conv),
        _post("t3", "@ravi", "refund received, resolved, great support thanks", "2026-07-01T12:00:00Z", conv),
    ])
    db.upsert_analysis(_analysis("t1", sentiment="negative", score=-0.6))
    db.upsert_analysis(_analysis("t3", sentiment="positive", score=0.8, intent="praise"))
    resolution.build_interactions()
    fi = db.df("SELECT * FROM fact_interaction WHERE conversation_id='thread:1'")
    assert len(fi) == 1
    r = fi.iloc[0]
    assert int(r["resolved"]) == 1
    assert int(r["customer_satisfied"]) == 1
    assert r["recovery_delta"] is not None and float(r["recovery_delta"]) > 0
    assert r["response_latency_min"] is not None


def test_resolution_no_bank_reply_unresolved(fresh_db):
    db.upsert_posts([_post("u1", "@meera", "@AxisBank upi down", "2026-07-01T10:00:00Z", "thread:2")])
    db.upsert_analysis(_analysis("u1"))
    resolution.build_interactions()
    r = db.df("SELECT * FROM fact_interaction WHERE conversation_id='thread:2'").iloc[0]
    assert int(r["resolved"]) == 0 and int(r["n_bank_replies"]) == 0


# ---------------------------------------------------------------- facts exclude bank voice
def test_fact_mention_excludes_axis_handles(fresh_db):
    db.upsert_posts([
        _post("m1", "@ravi", "upi failed", "2026-07-01T10:00:00Z"),
        _post("m2", "@AxisBankSupport", "we are here to help", "2026-07-01T10:00:00Z"),
    ])
    db.upsert_analysis(_analysis("m1"))
    db.upsert_analysis(_analysis("m2", sentiment="neutral"))
    build.build_dim_author()
    build.build_facts()
    authors = set(db.df("SELECT author FROM fact_mention")["author"])
    assert "@ravi" in authors and "@AxisBankSupport" not in authors


# ---------------------------------------------------------------- RM mart + xsell
def test_rm_mart_pain_and_crosssell(fresh_db):
    db.upsert_rows("dim_customer", [dict(customer_key="C1", customer_name="Ravi K", segment="Priority",
                                         rm_id="R1", city="Bengaluru", clv=500000, risk_flag=0,
                                         products_held="savings account", updated_at=db.now())],
                   "customer_key", build.DIM_CUSTOMER_COLS)
    db.upsert_rows("dim_rm", [dict(rm_id="R1", rm_name="Priya", branch="KOR", region="South")],
                   "rm_id", build.DIM_RM_COLS)
    db.upsert_rows("bridge_handle_customer", [dict(author="@ravi", customer_key="C1",
                   match_method="crm", confidence=1.0, verified_by="test",
                   effective_from=db.now(), effective_to=None)], "author", build.BRIDGE_COLS)
    db.upsert_posts([_post("r1", "@ravi", "upi failed money debited", "2026-07-01T10:00:00Z")])
    db.upsert_analysis(_analysis("r1"))
    build.build_dim_author()
    build.build_facts()
    resolution.build_interactions()
    build.build_marts()
    m = db.df("SELECT * FROM mart_rm_enablement WHERE customer_key='C1'")
    assert len(m) == 1
    row = m.iloc[0]
    assert row["top_pain_area"] in ("upi", "payments_upi")
    assert row["cross_sell_product"] and row["cross_sell_product"] != ""
    assert row["talking_point"]


def test_xsell_rules_direct():
    prod, pitch = xsell.recommend({"savings account"}, "levy_of_charges", "cards", "complaint")
    assert "card" in prod.lower()
    prod2, _ = xsell.recommend({"credit card", "savings account", "personal loan", "fixed deposit",
                                "home loan", "insurance", "demat"}, "not_applicable", "none", "other")
    assert prod2                                   # fallback still returns something


def test_kpis_and_recovery_rate(fresh_db):
    conv = "thread:1"
    db.upsert_posts([
        _post("t1", "@ravi", "@AxisBank upi failed", "2026-07-01T10:00:00Z", conv),
        _post("t2", "@AxisBankSupport", "refund raised please DM", "2026-07-01T11:00:00Z", conv),
        _post("t3", "@ravi", "resolved thanks great", "2026-07-01T12:00:00Z", conv),
    ])
    db.upsert_analysis(_analysis("t1", score=-0.6))
    db.upsert_analysis(_analysis("t3", sentiment="positive", score=0.8, intent="praise"))
    build.build_dim_author()
    build.build_facts()
    resolution.build_interactions()
    build.build_marts()
    k = db.df("SELECT * FROM mart_kpis").iloc[0]
    assert int(k["total_mentions"]) >= 1
    assert 0 <= float(k["sentiment_recovery_rate"]) <= 100
