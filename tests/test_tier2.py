"""Tier-2 intelligence: churn prediction, forecast, NER."""
import db
from analytics import intelligence


def _seed(sid, author, text, sentiment, score, intent="complaint", churn=0,
          rbi="upi", created="2026-07-01T10:00:00Z"):
    db.upsert_posts([dict(source_id=sid, source="twitter", author=author, text=text,
                          url="http://x/" + sid, created_at=created, engagement=10, view_count=100, lang="en")])
    db.upsert_analysis(dict(source_id=sid, sentiment=sentiment, score=score, emotion="anger",
                            emotion_intensity=4, sarcasm=0, intent=intent, urgency="high", urgency_reason="x",
                            product="Axis Magnus", root_cause="rc", rbi_category=rbi, recommended_team="payments_upi",
                            recommended_action="fix", churn_risk=churn, fraud_signal=0, fraud_type="none",
                            pii_present=0, theme="t", summary="s", confidence=0.9, aspects_json="[]",
                            cluster_id=None, model="groq", text_masked=text, pii_types=""))


# ---- pure ----
def test_forecast_series_rising():
    preds, trend = intelligence.forecast_series([1, 2, 3, 4, 5])
    assert trend == "rising" and preds[0] >= 5


def test_forecast_series_flat():
    _, trend = intelligence.forecast_series([3, 3, 3, 3])
    assert trend == "flat"


def test_extract_entities():
    ents = dict(intelligence.extract_entities("Axis Magnus lounge in Mumbai was great"))
    assert ents.get("Magnus") == "product"
    assert ents.get("Mumbai") == "city"
    assert intelligence.extract_entities("nothing here") == []


# ---- seeded ----
def test_build_churn_ranks_threat_top(fresh_db):
    _seed("c1", "@leaver", "closing my account, moving to HDFC, worst service", "negative", -0.8,
          intent="churn_threat", churn=1)
    _seed("c2", "@happy", "great app love it", "positive", 0.7, intent="praise")
    n = intelligence.build_churn()
    assert n >= 1
    top = db.df("SELECT * FROM mart_churn_risk ORDER BY churn_prob DESC").iloc[0]
    assert top["entity_key"] == "@leaver"
    assert 0.0 <= float(top["churn_prob"]) <= 1.0


def test_build_forecast(fresh_db):
    for i in range(5):
        _seed(f"f{i}", "@a", "upi failed", "negative", -0.5, created=f"2026-07-0{i+1}T10:00:00Z")
    n = intelligence.build_forecast()
    assert n >= 1
    trends = set(db.df("SELECT DISTINCT trend FROM mart_forecast")["trend"])
    assert trends <= {"rising", "falling", "flat"}


def test_build_entities(fresh_db):
    _seed("e1", "@a", "Axis Magnus card issue in Mumbai branch", "negative", -0.3)
    intelligence.build_entities()
    ents = set(db.df("SELECT entity FROM mart_entities")["entity"])
    assert "Magnus" in ents and "Mumbai" in ents
