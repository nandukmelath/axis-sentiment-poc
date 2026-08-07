"""Generate full_db.ipynb — one section per table, so the whole database is browsable
inside Jupyter itself (not an HTML export).

Run:  python tools/make_full_db_nb.py
Then: jupyter nbconvert --execute --inplace full_db.ipynb
"""
import json
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "axis.db")
OUT = os.path.join(ROOT, "full_db.ipynb")

FULL_UNDER = 200      # dump entire table below this
SAMPLE = 150          # otherwise show this many rows

GROUPS = [
    ("Pipeline core — bronze to silver",
     ["raw_posts", "clean_posts", "analysis", "clusters", "scored_posts"]),
    ("Star schema — fact tables",
     ["fact_mention", "fact_daily", "fact_aspect_sentiment", "fact_interaction"]),
    ("Star schema — dimensions",
     ["dim_author", "dim_source", "dim_team", "dim_category", "dim_date",
      "dim_product", "dim_customer", "dim_rm", "bridge_handle_customer"]),
    ("Insight marts — what the dashboard reads",
     ["mart_kpis", "mart_channel", "mart_competitor_sov", "mart_product_scorecard",
      "mart_churn_risk", "mart_fraud", "mart_team_queue", "mart_geo", "mart_trends",
      "mart_forecast", "mart_influencers", "mart_entities", "mart_rm_enablement",
      "mart_admin_analytics"]),
    ("Operations & governance",
     ["alerts", "reply_drafts", "translations", "competitor_posts", "run_metrics",
      "eval_history", "audit_log"]),
    ("Views", ["vw_mention", "vw_daily_sentiment"]),
]

NOTES = {
    "raw_posts": "Bronze layer — every mention exactly as collected. Contains unmasked text.",
    "clean_posts": "Beam output — de-duplication hash, spam flag, PII-masked text.",
    "analysis": "The AI read: 27 columns per post, from sentiment to recommended action.",
    "clusters": "Posts grouped into themes. Fragmented — most are singletons.",
    "scored_posts": "The view the dashboard reads: raw_posts joined to analysis.",
    "dim_author": "Type-2 slowly-changing dimension — 7,866 rows for ~1,200 handles "
                  "because a cumulative counter is tracked as an attribute.",
    "fact_interaction": "Reconstructed complaint threads. Only 4 have a detected bank reply.",
    "mart_kpis": "The single headline row the executive dashboard reads.",
    "mart_competitor_sov": "Peer comparison. Collection is asymmetric — only "
                           "pct_negative and avg_score are like-for-like.",
    "audit_log": "Populated when someone runs a masked export from the dashboard.",
}


def cell_code(src):
    return {"cell_type": "code", "execution_count": None, "metadata": {},
            "outputs": [], "source": src}


def cell_md(src):
    return {"cell_type": "markdown", "metadata": {}, "source": src}


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    cur = con.cursor()
    objs = cur.execute("""SELECT name, type FROM sqlite_master
                          WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'
                          ORDER BY name""").fetchall()
    kinds = dict(objs)
    counts = {}
    for n, _ in objs:
        try:
            counts[n] = cur.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0]
        except Exception:
            counts[n] = 0
    con.close()

    known = {n for _, names in GROUPS for n in names}
    groups = [(t, [n for n in names if n in kinds]) for t, names in GROUPS]
    extra = [n for n in kinds if n not in known]
    if extra:
        groups.append(("Other", sorted(extra)))

    total = sum(counts.values())
    cells = [
        cell_md([
            "# Axis Bank — the complete database\n",
            "\n",
            f"**{len(kinds)} tables and views · {total:,} rows · "
            f"{os.path.getsize(DB)/1024/1024:.1f} MB**\n",
            "\n",
            "Every object in `axis.db`, rendered inline. Tables with "
            f"{FULL_UNDER} rows or fewer are shown in full; larger ones show the first "
            f"{SAMPLE} rows.\n",
            "\n",
            "Connection is **read-only** (`mode=ro`) — nothing here can modify the data.\n",
            "\n",
            "> Run all: **Kernel → Restart Kernel and Run All Cells**",
        ]),
        cell_code([
            "import sqlite3\n",
            "import pandas as pd\n",
            "from IPython.display import display, Markdown\n",
            "\n",
            "pd.set_option('display.max_columns', 80)\n",
            "pd.set_option('display.width', 250)\n",
            "pd.set_option('display.max_colwidth', 70)\n",
            "pd.set_option('display.max_rows', 200)\n",
            "\n",
            f"DB = r\"{DB}\"\n",
            "con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=15)\n",
            "\n",
            "def show(table, limit=None):\n",
            "    \"\"\"Print schema + row count, then render the rows.\"\"\"\n",
            "    n = pd.read_sql(f'SELECT count(*) c FROM \"{table}\"', con)['c'][0]\n",
            "    cols = pd.read_sql(f'PRAGMA table_info(\"{table}\")', con)\n",
            "    lim = '' if limit is None else f' LIMIT {limit}'\n",
            "    df = pd.read_sql(f'SELECT * FROM \"{table}\"{lim}', con)\n",
            "    shown = 'all rows' if limit is None else f'first {len(df):,} of {n:,}'\n",
            "    display(Markdown(\n",
            "        f\"**`{table}`** — {n:,} rows · {len(cols)} columns · _{shown}_\\n\\n\"\n",
            "        f\"`{' , '.join(cols['name'])}`\"))\n",
            "    display(df)\n",
            "    return df\n",
            "\n",
            "print('connected (read-only):', DB)",
        ]),
        cell_md([
            "## Contents\n\n",
            *[f"- **{t}** — {', '.join(f'`{n}`' for n in names)}\n"
              for t, names in groups if names],
        ]),
        cell_md([
            "---\n## Inventory — everything at a glance",
        ]),
        cell_code([
            "inv = pd.read_sql(\"\"\"SELECT name AS object, type FROM sqlite_master\n",
            "                      WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'\n",
            "                      ORDER BY name\"\"\", con)\n",
            "inv['rows'] = [pd.read_sql(f'SELECT count(*) c FROM \"{n}\"', con)['c'][0]\n",
            "               for n in inv['object']]\n",
            "inv = inv.sort_values('rows', ascending=False).reset_index(drop=True)\n",
            "print(f\"{len(inv)} objects · {inv['rows'].sum():,} total rows\")\n",
            "inv",
        ]),
    ]

    for title, names in groups:
        if not names:
            continue
        cells.append(cell_md([f"---\n# {title}"]))
        for n in names:
            c = counts.get(n, 0)
            note = NOTES.get(n)
            head = [f"## `{n}`  ·  {c:,} rows  ·  _{kinds.get(n,'table')}_\n"]
            if note:
                head += ["\n", note]
            cells.append(cell_md(head))
            limit = "None" if c <= FULL_UNDER else str(SAMPLE)
            cells.append(cell_code([f"show('{n}', limit={limit})"]))

    nb = {"cells": cells,
          "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python",
                                      "name": "python3"},
                       "language_info": {"name": "python", "version": "3.10"}},
          "nbformat": 4, "nbformat_minor": 5}

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(nb, fh, indent=1)
    print(f"wrote {OUT}")
    print(f"{len(cells)} cells · {len(kinds)} objects · {total:,} rows")


if __name__ == "__main__":
    main()
