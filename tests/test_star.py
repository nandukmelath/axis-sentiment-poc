"""Star layer + the mixed-date-format fix (regression for the 87%-NULL created_date bug)."""
import pandas as pd
import db
from warehouse import build, star


def test_parse_dt_handles_mixed_formats():
    s = pd.Series([
        "2026-07-06T23:03:38-07:00",       # ISO w/ offset (appstore/twitter)
        "Mon, 29 Jun 2026 11:37:04 +0000",  # RFC-822 (news/technofino)
        "2026-07-07T19:15:07+00:00",        # reddit
        "2026-07-08",                        # date-only
        "not-a-date",                        # -> NaT
    ])
    out = db.parse_dt(s)
    assert out.notna().sum() == 4                      # only the garbage is NaT
    assert out.isna().iloc[-1]
    # scalar path
    assert db.parse_dt("Tue, 07 Jul 2026 11:37:04 +0000").year == 2026


def _post(sid, source, author, created_at):
    return dict(source_id=sid, source=source, author=author, author_name=author,
                text=f"text {sid}", url=f"http://x/{sid}", created_at=created_at,
                engagement=5, conversation_id=None, lang="en")


def _an(sid, sentiment="negative", intent="complaint", team="payments_upi", rbi="upi"):
    return dict(source_id=sid, sentiment=sentiment, score=-0.5, emotion="frustration",
                emotion_intensity=4, sarcasm=0, intent=intent, urgency="high",
                urgency_reason="x", product="Axis Mobile", root_cause="rc", rbi_category=rbi,
                recommended_team=team, recommended_action="fix", churn_risk=0, fraud_signal=0,
                fraud_type="none", pii_present=0, theme="t", summary="s", confidence=0.9,
                aspects_json="[]", cluster_id=None, model="groq", text_masked=f"c{sid}", pii_types="")


def test_star_layer_end_to_end(fresh_db):
    # three sources, THREE different date formats — the exact mix that NULLed dates before the fix
    db.upsert_posts([
        _post("s1", "twitter", "@a", "2026-07-01T10:00:00+00:00"),
        _post("s2", "news", "@b", "Mon, 29 Jun 2026 11:37:04 +0000"),
        _post("s3", "technofino", "@c", "2026-07-03"),
    ])
    for sid in ("s1", "s2", "s3"):
        db.upsert_analysis(_an(sid))
    build.main("all")

    # 1. the date fix — every fact got a created_date + date_key (was ~13% before)
    fm = db.df("SELECT source, created_date, date_key, source_key FROM fact_mention ORDER BY source_id")
    assert len(fm) == 3
    assert fm["created_date"].notna().all(), "mixed-format dates must all parse"
    assert fm["date_key"].notna().all()
    assert set(fm["source_key"]) == {"twitter", "news", "technofino"}

    # 2. conformed dims seeded
    assert len(db.df("SELECT 1 FROM dim_source")) == len(star.SOURCE_SEED)
    assert len(db.df("SELECT 1 FROM dim_team")) == len(star.TEAM_SEED)
    assert len(db.df("SELECT 1 FROM dim_category")) == len(star.CATEGORY_SEED)

    # 3. dim_date covers every fact date
    orphans = db.df("SELECT f.date_key FROM fact_mention f "
                    "LEFT JOIN dim_date d ON f.date_key=d.date_key WHERE d.date_key IS NULL")
    assert orphans.empty, "every fact date_key must exist in dim_date"

    # 4. fact_daily reconciles to fact_mention count
    fd = db.df("SELECT SUM(mentions) m FROM fact_daily").iloc[0]["m"]
    assert int(fd) == 3

    # 5. semantic view joins cleanly + carries silver enrichment
    vw = db.df("SELECT source_type, emotion, team_name, category_name FROM vw_mention")
    assert len(vw) == 3
    assert set(vw["source_type"]) == {"social", "news", "forum"}
    assert vw["team_name"].notna().all()

    # 6. channel mart rolls up by source_type
    ch = db.df("SELECT source_type, mentions FROM mart_channel ORDER BY source_type")
    assert set(ch["source_type"]) == {"social", "news", "forum"}
    assert int(ch["mentions"].sum()) == 3
