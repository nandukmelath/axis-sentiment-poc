"""Pytest bootstrap — isolate the DB to a temp SQLite file BEFORE importing db,
so tests never touch the real axis.db, and add the project root to sys.path."""
import os
import sys
import tempfile
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_TMPDIR = tempfile.mkdtemp(prefix="axis_test_")
# Default to an isolated temp SQLite; set TEST_DATABASE_URL to run the suite on Postgres
# (dual-dialect verification) — point it at a DEDICATED test db (the fixture drops tables).
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or \
    ("sqlite:///" + os.path.join(_TMPDIR, "test.db").replace("\\", "/"))
os.environ["PII_MASK"] = "1"
os.environ["CASCADE"] = "1"

import pytest  # noqa: E402


@pytest.fixture()
def fresh_db():
    """A clean gold+silver schema in the temp DB for each test that needs one."""
    import db
    from warehouse import build
    # drop + recreate the tables we exercise so tests are independent
    with db.get_engine().begin() as c:
        from sqlalchemy import text
        # views must go first — PG blocks dropping any table a view depends on
        for v in ["scored_posts", "vw_mention", "vw_daily_sentiment"]:
            c.execute(text(f"DROP VIEW IF EXISTS {v}"))
        for t in ["raw_posts", "analysis", "clusters", "clean_posts", "dim_author", "dim_customer",
                  "dim_rm", "dim_product", "bridge_handle_customer", "fact_mention",
                  "fact_aspect_sentiment", "fact_interaction", "mart_rm_enablement",
                  "mart_admin_analytics", "mart_kpis",
                  "mart_product_scorecard", "mart_influencers", "mart_team_queue", "mart_fraud",
                  "mart_trends", "mart_geo", "mart_competitor_sov", "competitor_posts",
                  "reply_drafts", "alerts", "audit_log",
                  "mart_churn_risk", "mart_forecast", "mart_entities", "translations",
                  "eval_history", "run_metrics",
                  "dim_date", "dim_source", "dim_team", "dim_category", "fact_daily", "mart_channel"]:
            c.execute(text(f"DROP TABLE IF EXISTS {t}"))
        # ADR-001 gate snapshots — drop any left by a crashed gate test
        for t in getattr(db, "GATED_TABLES", []):
            c.execute(text(f'DROP TABLE IF EXISTS "{t}__bak"'))
    build.ensure_tables()
    from analytics import features
    features.ensure_tables()
    return db
