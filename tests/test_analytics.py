"""Product feature layer — geo/trend pure fns + marts + actions (alerts/respond/audit)."""
import db
from analytics import features, actions


# ---------------------------------------------------------------- pure helpers
def test_infer_city():
    assert features.infer_city("issue at Axis Bengaluru branch") == ("Bengaluru", "South")
    assert features.infer_city("mumbai atm down") == ("Mumbai", "West")
    assert features.infer_city("no location here") == (None, None)


def test_zscores_flat_is_zero():
    assert features._zscores([5, 5, 5, 5]) == [0.0, 0.0, 0.0, 0.0]


def test_zscores_spike_positive():
    zs = features._zscores([1, 1, 1, 10])
    assert zs[-1] > 1.5          # last value is a clear spike


# ---------------------------------------------------------------- seed + marts
def _seed(fresh_db, sid, author, text, sentiment, score, intent="complaint", team="payments_upi",
          product="Axis Magnus", fraud=0, rbi="upi", created="2026-07-01T10:00:00Z", eng=100):
    db.upsert_posts([dict(source_id=sid, source="twitter", author=author, text=text,
                          url=f"http://x/{sid}", created_at=created, engagement=eng, view_count=eng * 10,
                          lang="en")])
    db.upsert_analysis(dict(source_id=sid, sentiment=sentiment, score=score, emotion="anger",
                            emotion_intensity=4, sarcasm=0, intent=intent, urgency="high", urgency_reason="x",
                            product=product, root_cause="rc", rbi_category=rbi, recommended_team=team,
                            recommended_action="fix", churn_risk=0, fraud_signal=fraud, fraud_type="phishing" if fraud else "none",
                            pii_present=0, theme="t", summary=f"summary {sid}", confidence=0.9, aspects_json="[]",
                            cluster_id=None, model="groq", text_masked=text, pii_types=""))


def test_product_scorecard(fresh_db):
    _seed(fresh_db, "p1", "@a", "magnus fee issue", "negative", -0.6, product="Axis Magnus")
    _seed(fresh_db, "p2", "@b", "magnus lounge great", "positive", 0.7, intent="praise", product="Axis Magnus")
    features.build_product_scorecard()
    m = db.df("SELECT * FROM mart_product_scorecard WHERE product='Axis Magnus'").iloc[0]
    assert int(m["mentions"]) == 2 and int(m["complaints"]) == 1
    assert -100 <= float(m["nps_proxy"]) <= 100


def test_influencers_ranked_by_reach(fresh_db):
    _seed(fresh_db, "i1", "@small", "axis bad", "negative", -0.5, eng=5)
    _seed(fresh_db, "i2", "@big", "axis worst", "negative", -0.7, eng=9000)
    features.build_influencers()
    top = db.df("SELECT author, reach FROM mart_influencers ORDER BY reach DESC").iloc[0]
    assert top["author"] == "@big"


def test_team_queue_and_fraud(fresh_db):
    _seed(fresh_db, "t1", "@a", "upi failed", "negative", -0.6, team="payments_upi")
    _seed(fresh_db, "f1", "@b", "otp scam fake axis", "negative", -0.8, team="fraud_cyber", fraud=1)
    features.build_team_queue()
    features.build_fraud_board()
    tq = db.df("SELECT * FROM mart_team_queue WHERE team='payments_upi'").iloc[0]
    assert int(tq["open_items"]) >= 1
    fr = db.df("SELECT SUM(cnt) c FROM mart_fraud").iloc[0]["c"]
    assert int(fr) >= 1


def test_geo_mart(fresh_db):
    _seed(fresh_db, "g1", "@a", "Axis Mumbai branch rude staff", "negative", -0.5)
    _seed(fresh_db, "g2", "@b", "Axis Bengaluru app down", "negative", -0.6)
    features.build_geo()
    cities = set(db.df("SELECT city FROM mart_geo")["city"])
    assert {"Mumbai", "Bengaluru"} <= cities


def test_trends_anomaly(fresh_db):
    # long flat baseline (1/day) so a single spike day clears z>=2
    for i in range(8):
        _seed(fresh_db, f"d{i}", "@a", "upi failed", "negative", -0.5,
              created=f"2026-07-0{i+1}T10:00:00Z")
    for j in range(12):                                  # spike on 2026-07-09
        _seed(fresh_db, f"s{j}", f"@u{j}", "upi failed massive outage", "negative", -0.7,
              created="2026-07-09T10:00:00Z")
    features.build_trends()
    anom = db.df("SELECT * FROM mart_trends WHERE anomaly=1")
    assert len(anom) >= 1


def test_competitor_sov(fresh_db):
    _seed(fresh_db, "a1", "@a", "axis fine", "neutral", 0.0)
    db.upsert_rows("competitor_posts", [dict(source_id="c1", brand="HDFC Bank", source="news",
                   author="news", text="hdfc great", url="", created_at="2026-07-01",
                   sentiment="positive", score=0.5, fetched_at=db.now())], "source_id",
                   ["source_id", "brand", "source", "author", "text", "url", "created_at",
                    "sentiment", "score", "fetched_at"])
    features.build_competitor_sov()
    sov = db.df("SELECT * FROM mart_competitor_sov")
    assert set(sov["brand"]) >= {"HDFC Bank"}
    assert abs(sov["share_of_voice"].sum() - 100) < 1.0


# ---------------------------------------------------------------- actions
def test_audit_and_masked_export(fresh_db):
    _seed(fresh_db, "e1", "@a", "call 9876543210 card 4111 1111 1111 1111", "negative", -0.5)
    # store the masked version like the pipeline does
    db.set_masked("e1", "call XXXXXXXX10 card XXXX-XXXX-XXXX-1111", "phone,card")
    df = actions.export_masked("SELECT r.source_id, r.text, a.text_masked FROM raw_posts r "
                               "JOIN analysis a ON r.source_id=a.source_id", actor="tester")
    assert "9876543210" not in " ".join(df["text"].astype(str))     # export used masked text
    assert int(db.df("SELECT COUNT(*) n FROM audit_log").iloc[0]["n"]) == 1


def test_alerts_fire_on_fraud(fresh_db):
    for i in range(6):
        _seed(fresh_db, f"fr{i}", f"@u{i}", "otp scam fake axis helpline", "negative", -0.8,
              team="fraud_cyber", fraud=1)
    n = actions.build_alerts()
    kinds = set(db.df("SELECT kind FROM alerts")["kind"])
    assert "fraud" in kinds


def test_draft_replies_template_without_llm(fresh_db, monkeypatch):
    _seed(fresh_db, "r1", "@a", "upi failed money debited refund now", "negative", -0.7)
    db.set_masked("r1", "upi failed money debited refund now", "")
    # force the LLM path to fail -> template fallback
    import analyze.llm as llm
    monkeypatch.setattr(llm, "generate_text", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no key")))
    actions.draft_replies(limit=5)
    d = db.df("SELECT * FROM reply_drafts WHERE source_id='r1'").iloc[0]
    assert d["draft"] and d["model"] == "template"
