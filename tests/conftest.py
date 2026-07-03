"""Pytest bootstrap — isolate the DB to a temp SQLite file BEFORE importing db,
so tests never touch the real axis.db, and add the project root to sys.path."""
import os
import sys
import tempfile
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_TMPDIR = tempfile.mkdtemp(prefix="axis_test_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMPDIR, "test.db").replace("\\", "/")
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
        for t in ["raw_posts", "analysis", "clusters", "clean_posts", "dim_author", "dim_customer",
                  "dim_rm", "dim_product", "bridge_handle_customer", "fact_mention",
                  "fact_aspect_sentiment", "fact_interaction", "mart_rm_enablement",
                  "mart_admin_analytics", "mart_kpis"]:
            c.execute(text(f"DROP TABLE IF EXISTS {t}"))
    build.ensure_tables()
    return db
