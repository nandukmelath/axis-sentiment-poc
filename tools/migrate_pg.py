"""Migrate the local SQLite DB into the target Postgres (DATABASE_URL), then rebuild all
derived tables on Postgres — proving the system runs on a production DB.

  DATABASE_URL=postgresql+psycopg2://axis:axis@localhost:5432/axis python -m tools.migrate_pg
"""
import os
import pandas as pd
from sqlalchemy import create_engine, text

assert "postgres" in os.getenv("DATABASE_URL", ""), "Set DATABASE_URL to the Postgres target first."

import db                                   # binds to the Postgres target via DATABASE_URL
from config import DB_PATH

SOURCE = ["raw_posts", "analysis", "clusters", "clean_posts", "dim_customer", "dim_rm",
          "dim_product", "bridge_handle_customer", "competitor_posts"]


def main():
    print(f"target: {db.DB_URL}")
    db.init_db()
    from warehouse.build import ensure_tables as gold
    from analytics.features import ensure_tables as feat
    gold()
    feat()

    local = create_engine("sqlite:///" + DB_PATH.replace("\\", "/"))
    target = db.get_engine()
    for t in SOURCE:
        try:
            rows = pd.read_sql_query(f"SELECT * FROM {t}", local)
        except Exception as e:
            print(f"  {t}: skip ({str(e)[:50]})")
            continue
        rows = rows.astype(object).where(pd.notnull(rows), None)   # NaN -> NULL
        with target.begin() as c:
            c.execute(text(f"DELETE FROM {t}"))
        if len(rows):
            rows.to_sql(t, target, if_exists="append", index=False, chunksize=500)
        print(f"  {t}: {len(rows)} rows")

    # rebuild the derived layer on Postgres
    from warehouse import build
    from analytics import features, intelligence, ops
    build.main("all")
    features.build_all()
    intelligence.build_all()
    ops.run_all()
    print("migration + rebuild complete on Postgres.")


if __name__ == "__main__":
    main()
