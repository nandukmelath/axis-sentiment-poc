"""Query + view the DB from the terminal. Works on SQLite (default) or Postgres
(follows DATABASE_URL, same as the app).

Usage:
  python query.py                     # overview: tables + row counts + presets
  python query.py tables              # list tables with row counts
  python query.py schema analysis     # columns of a table
  python query.py sentiment           # a named preset (see list below)
  python query.py "SELECT * FROM analysis WHERE fraud_signal=1"   # any SQL
  python query.py -i                  # interactive SQL prompt
"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # avoid Windows cp1252 crashes
except Exception:
    pass

import pandas as pd
import db

pd.set_option("display.width", 200)
pd.set_option("display.max_colwidth", 80)
pd.set_option("display.max_rows", 100)

PRESETS = {
    "sources":   "SELECT source, count(*) n FROM raw_posts GROUP BY source ORDER BY n DESC",
    "sentiment": "SELECT sentiment, count(*) n FROM analysis GROUP BY sentiment ORDER BY n DESC",
    "urgency":   "SELECT urgency, count(*) n FROM analysis GROUP BY urgency ORDER BY n DESC",
    "issues":    "SELECT title, size, top_team, recent_share FROM clusters ORDER BY size DESC",
    "fraud":     "SELECT source, substr(text,1,90) txt FROM raw_posts r JOIN analysis a USING(source_id) WHERE fraud_signal=1",
    "urgent":    "SELECT urgency, source, recommended_team, substr(text,1,70) txt FROM raw_posts r JOIN analysis a USING(source_id) WHERE urgency IN ('critical','high') ORDER BY urgency",
    "recent":    "SELECT created_at, source, sentiment, urgency, substr(text,1,70) txt FROM raw_posts r JOIN analysis a USING(source_id) ORDER BY fetched_at DESC LIMIT 25",
    "models":    "SELECT model, count(*) n FROM analysis GROUP BY model ORDER BY n DESC",
}

TABLES = ["raw_posts", "analysis", "clusters"]


def run(sql):
    try:
        print(db.df(sql).to_string(index=False))
    except Exception as e:
        print("SQL error:", str(e)[:200])


def overview():
    print(f"DB [{db.DIALECT}]: {db.DB_URL}\n")
    print("TABLES:")
    for t in TABLES:
        try:
            n = db.df(f"SELECT count(*) c FROM {t}").c.iloc[0]
            print(f"  {t:<12} {n} rows")
        except Exception:
            print(f"  {t:<12} (missing)")
    print("\nPRESETS:", ", ".join(PRESETS))
    print('Run any SQL:  python query.py "SELECT ... "   or   python query.py -i')


def main():
    args = sys.argv[1:]
    if not args:
        overview()
        return
    if args[0] == "-i":
        print(f"interactive SQL on [{db.DIALECT}]. type 'quit' to exit.")
        while True:
            try:
                q = input("sql> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if q.lower() in ("quit", "exit", "q"):
                break
            if q:
                run(q)
        return
    if args[0] == "tables":
        overview()
        return
    if args[0] == "schema":
        t = args[1] if len(args) > 1 else "analysis"
        run(f"SELECT * FROM {t} LIMIT 0" if db.DIALECT != "sqlite" else f"PRAGMA table_info({t})")
        return
    if args[0] in PRESETS:
        run(PRESETS[args[0]])
        return
    run(" ".join(args))


if __name__ == "__main__":
    main()
