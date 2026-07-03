"""One-time migration: copy the local SQLite DB into Postgres (Supabase/Neon).

Usage (PowerShell):
  $env:DATABASE_URL="postgresql://postgres.xxxx:PASSWORD@aws-0-...pooler.supabase.com:5432/postgres?sslmode=require"
  python migrate_to_pg.py

It creates the schema (tables + scored_posts view) on the target, then copies
raw_posts / analysis / clusters from local axis.db. Idempotent-ish: uses append,
so run it once (or TRUNCATE the target tables before re-running).
"""
import os
import pandas as pd
from sqlalchemy import create_engine

url = os.getenv("DATABASE_URL", "")
assert "postgres" in url, (
    "Set DATABASE_URL to your Supabase Postgres URL first "
    "(postgresql://...  — copy from Supabase > Project Settings > Database > Connection string > URI, "
    "append ?sslmode=require)")

import db                      # binds to DATABASE_URL (the Postgres target)
from config import DB_PATH

print(f"target: {db.DB_URL}")
db.init_db()                   # create tables + scored_posts view on Postgres

local = create_engine("sqlite:///" + DB_PATH.replace("\\", "/"))
target = db.get_engine()

for tbl in ["raw_posts", "analysis", "clusters"]:
    try:
        rows = pd.read_sql_query(f"SELECT * FROM {tbl}", local)
    except Exception as e:
        print(f"  {tbl}: local read skipped ({str(e)[:60]})")
        continue
    if len(rows):
        rows.to_sql(tbl, target, if_exists="append", index=False)
    print(f"  copied {len(rows)} rows -> {tbl}")

print("\nmigration done. Your data now lives in Supabase.")
print("Point the app at it: put DATABASE_URL in .env (same value). All scripts + the dashboard follow it.")
