"""ADR-001 DQ gate — snapshot/restore keeps last-good data when a rebuild would publish garbage."""
from sqlalchemy import inspect
import db
from warehouse import build


def _seed(fresh_db):
    db.upsert_posts([dict(source_id="g1", source="twitter", author="@a", author_name="a",
                          text="upi failed money debited", url="http://x/g1",
                          created_at="2026-07-01T10:00:00Z", engagement=5,
                          conversation_id=None, lang="en")])
    db.upsert_analysis(dict(source_id="g1", sentiment="negative", score=-0.5, emotion="anger",
                            emotion_intensity=4, sarcasm=0, intent="complaint", urgency="high",
                            urgency_reason="x", product="Axis", root_cause="rc", rbi_category="upi",
                            recommended_team="payments_upi", recommended_action="fix", churn_risk=0,
                            fraud_signal=0, fraud_type="none", pii_present=0, theme="t", summary="s",
                            confidence=0.9, aspects_json="[]", cluster_id=None, model="groq",
                            text_masked="c", pii_types=""))
    db.upsert_rows("clean_posts", [dict(source_id="g1", clean_text="upi failed", lang="en",
                   text_hash="h1", is_duplicate=0, spam_flag=0, pii_types="", transformed_at=db.now())],
                   "source_id", db.CLEAN_COLS)
    build.main("all")   # full derived layer incl. star + views


def _n(t):
    return int(db.df(f"SELECT COUNT(*) n FROM {t}").iloc[0]["n"])


def test_snapshot_restore_roundtrip(fresh_db):
    _seed(fresh_db)
    good_fm, good_kpi = _n("fact_mention"), _n("mart_kpis")
    assert good_fm == 1 and good_kpi == 1

    db.snapshot_tables()                       # capture last-good
    # simulate a bad rebuild wiping gated tables
    db.execute("DELETE FROM fact_mention")
    db.execute("DELETE FROM mart_kpis")
    assert _n("fact_mention") == 0

    db.restore_tables()                        # roll back to last-good
    assert _n("fact_mention") == good_fm
    assert _n("mart_kpis") == good_kpi
    # live tables were never dropped -> the dependent view still resolves
    assert _n("vw_mention") == good_fm

    db.drop_snapshots()
    assert not inspect(db.get_engine()).has_table("fact_mention__bak")


def test_restore_is_noop_without_snapshot(fresh_db):
    _seed(fresh_db)
    n = _n("fact_mention")
    db.restore_tables()          # no __bak exists -> must not touch live data
    assert _n("fact_mention") == n
