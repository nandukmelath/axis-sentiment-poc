"""Tier-4 trust/ops: cost estimate, quality/drift, FastAPI layer."""
import db
from analytics import ops
from analytics.features import ensure_tables, EVAL_COLS


def _seed_analysis(sid, model="groq", conf=0.9):
    db.upsert_posts([dict(source_id=sid, source="twitter", author="@a", text="upi failed",
                          url="http://x/" + sid, created_at="2026-07-01T10:00:00Z", engagement=1, lang="en")])
    db.upsert_analysis(dict(source_id=sid, sentiment="negative", score=-0.5, emotion="anger",
                            emotion_intensity=3, sarcasm=0, intent="complaint", urgency="high", urgency_reason="x",
                            product="Axis", root_cause="", rbi_category="upi", recommended_team="payments_upi",
                            recommended_action="", churn_risk=0, fraud_signal=0, fraud_type="none", pii_present=0,
                            theme="", summary="s", confidence=conf, aspects_json="[]", cluster_id=None,
                            model=model, text_masked="upi failed", pii_types=""))


def test_estimate_cost(fresh_db):
    _seed_analysis("a1", model="gemini-2.5-flash")
    _seed_analysis("a2", model="vader-fast")     # free, excluded from llm_calls
    r = ops.estimate_cost()
    assert r["llm_calls"] == 1
    assert r["cost_usd_est"] >= 0 and r["tokens_est"] > 0


def test_record_quality(fresh_db):
    _seed_analysis("a1", model="groq", conf=0.9)
    q = ops.record_quality()
    assert "avg_confidence" in q
    assert int(db.df("SELECT COUNT(*) n FROM eval_history").iloc[0]["n"]) == 3


def test_drift_flags(fresh_db):
    ensure_tables()
    db.insert_rows("eval_history", [{"run_ts": "2026-07-01T00:00:00", "metric": "avg_confidence", "value": 0.9}], EVAL_COLS)
    db.insert_rows("eval_history", [{"run_ts": "2026-07-02T00:00:00", "metric": "avg_confidence", "value": 0.5}], EVAL_COLS)
    flags = ops.drift_flags()
    assert any(f["metric"] == "avg_confidence" for f in flags)


def test_api_endpoints(fresh_db):
    _seed_analysis("a1", model="groq")
    from warehouse import build
    build.main("all")
    from fastapi.testclient import TestClient
    from api.main import app
    c = TestClient(app)
    assert c.get("/health").json()["status"] == "ok"
    assert c.get("/kpis").status_code == 200
    assert isinstance(c.get("/clusters").json(), list)
    assert c.get("/rm/DOES_NOT_EXIST").status_code == 404


def test_api_key_enforced(fresh_db, monkeypatch):
    monkeypatch.setenv("AXIS_API_KEY", "secret123")
    from fastapi.testclient import TestClient
    from api.main import app
    c = TestClient(app)
    assert c.get("/kpis").status_code == 401                     # missing key
    assert c.get("/kpis", headers={"x-api-key": "secret123"}).status_code == 200
    assert c.get("/health").status_code == 200                    # health is open
