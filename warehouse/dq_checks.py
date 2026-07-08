"""Data-quality gate — runs after the warehouse build. Exits non-zero (fails the Airflow
task) if any hard check fails, so bad data can't silently reach the dashboard / marts.

Run:  python -m warehouse.dq_checks
"""
import sys
import db
from warehouse.build import ensure_tables
from analyze.schema import Sentiment, Urgency, Intent

VALID = {
    "sentiment": {e.value for e in Sentiment},
    "urgency": {e.value for e in Urgency},
    "intent": {e.value for e in Intent},
}


def _n(sql):
    return int(db.df(sql).iloc[0]["n"])


def run():
    """Return list of (name, ok, detail). Pure — no exit — so tests can call it."""
    ensure_tables()
    checks = []

    def chk(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    raw = _n("SELECT COUNT(*) n FROM raw_posts")
    chk("raw_posts not empty", raw > 0, f"{raw} rows")

    an = _n("SELECT COUNT(*) n FROM analysis")
    chk("analysis coverage >= 95%", (an >= 0.95 * raw) if raw else True, f"{an}/{raw} scored")

    for col, allowed in VALID.items():
        bad = [v for v in db.df(f"SELECT DISTINCT {col} v FROM analysis WHERE {col} IS NOT NULL")["v"]
               if v not in allowed]
        chk(f"{col} enum valid", not bad, f"invalid: {bad[:5]}")

    dupcur = db.df("SELECT author FROM dim_author WHERE is_current=1 GROUP BY author HAVING COUNT(*) > 1")
    chk("dim_author: one current row per handle", dupcur.empty, f"{len(dupcur)} handles with >1 current")

    fm = _n("SELECT COUNT(*) n FROM fact_mention")
    chk("fact_mention not empty", fm > 0, f"{fm} rows")

    orphan = _n("""SELECT COUNT(*) n FROM fact_mention f
                   LEFT JOIN raw_posts r ON f.source_id = r.source_id WHERE r.source_id IS NULL""")
    chk("no orphan fact_mention", orphan == 0, f"{orphan} orphans")

    cp = _n("SELECT COUNT(*) n FROM clean_posts")
    chk("clean_posts populated (transform ran)", cp > 0, f"{cp} rows")

    badrec = _n("""SELECT COUNT(*) n FROM fact_interaction
                   WHERE resolved=1 AND first_response_at IS NULL""")
    chk("resolved interactions have a response", badrec == 0, f"{badrec} resolved w/o response")

    kpi = _n("SELECT COUNT(*) n FROM mart_kpis")
    chk("mart_kpis has exactly one row", kpi == 1, f"{kpi} rows")

    # ---- star-layer integrity (guards the mixed-date fix + conformed model) ----
    dated = _n("SELECT COUNT(*) n FROM fact_mention WHERE created_date IS NOT NULL")
    chk("fact_mention created_date >= 95% (mixed-date parse)", dated >= 0.95 * fm if fm else True,
        f"{dated}/{fm} dated")

    dorphan = _n("""SELECT COUNT(*) n FROM fact_mention f
                    LEFT JOIN dim_date d ON f.date_key = d.date_key
                    WHERE f.date_key IS NOT NULL AND d.date_key IS NULL""")
    chk("dim_date covers all fact date_keys", dorphan == 0, f"{dorphan} uncovered")

    fd = _n("SELECT COALESCE(SUM(mentions),0) n FROM fact_daily")
    chk("fact_daily reconciles to dated facts", fd == dated, f"fact_daily={fd} vs dated={dated}")

    return checks


def main():
    checks = run()
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    print(f"DQ: {passed}/{total} checks passed")
    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
