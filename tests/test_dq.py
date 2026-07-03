"""Data-quality gate — passes on good data, flags the specific defects it should catch."""
import db
from warehouse import build, resolution, dq_checks


def _seed_good(fresh_db):
    db.upsert_posts([dict(source_id="r1", source="twitter", author="@ravi", text="upi failed money debited",
                          url="http://x/r1", created_at="2026-07-01T10:00:00Z", engagement=5, lang="en")])
    db.upsert_analysis(dict(source_id="r1", sentiment="negative", score=-0.5, emotion="anger",
                            emotion_intensity=4, sarcasm=0, intent="complaint", urgency="high",
                            urgency_reason="x", product="Axis", root_cause="upi", rbi_category="upi",
                            recommended_team="payments_upi", recommended_action="fix", churn_risk=0,
                            fraud_signal=0, fraud_type="none", pii_present=0, theme="upi", summary="s",
                            confidence=0.9, aspects_json="[]", cluster_id=None, model="groq",
                            text_masked="upi failed", pii_types=""))
    db.upsert_rows("clean_posts", [dict(source_id="r1", clean_text="upi failed", lang="en",
                   text_hash="h1", is_duplicate=0, spam_flag=0, pii_types="", transformed_at=db.now())],
                   "source_id", db.CLEAN_COLS)
    build.build_dim_author()
    build.build_facts()
    resolution.build_interactions()
    build.build_marts()


def test_dq_passes_on_good_data(fresh_db):
    _seed_good(fresh_db)
    checks = dq_checks.run()
    failed = [(n, d) for n, ok, d in checks if not ok]
    assert not failed, f"unexpected DQ failures: {failed}"


def test_dq_flags_invalid_enum(fresh_db):
    _seed_good(fresh_db)
    db.execute("UPDATE analysis SET sentiment='WILD' WHERE source_id='r1'")
    checks = dq_checks.run()
    names = [n for n, ok, _ in checks if not ok]
    assert any("sentiment enum" in n for n in names)


def test_dq_flags_orphan_fact(fresh_db):
    _seed_good(fresh_db)
    # create an orphan fact_mention row referencing a non-existent post
    db.insert_rows("fact_mention", [dict(source_id="ghost", author="@x", source="twitter",
                   created_date="2026-07-01", sentiment="negative", score=-0.1)],
                   ["source_id", "author", "source", "created_date", "sentiment", "score"])
    checks = dq_checks.run()
    names = [n for n, ok, _ in checks if not ok]
    assert any("orphan" in n for n in names)
