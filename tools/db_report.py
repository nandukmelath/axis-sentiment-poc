"""Render the ENTIRE axis.db as one self-contained, browsable HTML page.

Every table and view: schema, row count, and data. Small tables are dumped in full;
large ones show a capped sample so the page stays openable. Read-only throughout.

Run:  python tools/db_report.py [output.html]
"""
import html
import os
import sqlite3
import sys
from datetime import datetime

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "axis.db")
OUT = sys.argv[1] if len(sys.argv) > 1 else "AXIS_FULL_DB.html"

# Full dump below this many rows; above it we sample, so the page stays a sane size.
FULL_DUMP_UNDER = 200
SAMPLE_ROWS = 150
CELL_CHARS = 220

GROUPS = [
    ("Pipeline core", ["raw_posts", "clean_posts", "analysis", "clusters", "scored_posts"]),
    ("Star schema — facts", ["fact_mention", "fact_daily", "fact_aspect_sentiment",
                             "fact_interaction"]),
    ("Star schema — dimensions", ["dim_author", "dim_source", "dim_team", "dim_category",
                                  "dim_date", "dim_product", "dim_customer", "dim_rm",
                                  "bridge_handle_customer"]),
    ("Insight marts", ["mart_kpis", "mart_channel", "mart_competitor_sov",
                       "mart_product_scorecard", "mart_churn_risk", "mart_fraud",
                       "mart_team_queue", "mart_geo", "mart_trends", "mart_forecast",
                       "mart_influencers", "mart_entities", "mart_rm_enablement",
                       "mart_admin_analytics"]),
    ("Operations", ["alerts", "reply_drafts", "translations", "competitor_posts",
                    "run_metrics", "eval_history", "audit_log"]),
    ("Views", ["vw_mention", "vw_daily_sentiment"]),
]


def esc(v):
    if v is None:
        return '<span class="null">NULL</span>'
    s = str(v)
    if len(s) > CELL_CHARS:
        s = s[:CELL_CHARS] + "…"
    return html.escape(s)


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    objects = cur.execute(
        """SELECT name, type FROM sqlite_master
           WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'
           ORDER BY name""").fetchall()
    all_names = [r["name"] for r in objects]
    kinds = {r["name"]: r["type"] for r in objects}

    # anything not explicitly grouped still gets rendered, under "Other"
    grouped = {n for _, names in GROUPS for n in names}
    groups = [(t, [n for n in names if n in all_names]) for t, names in GROUPS]
    other = [n for n in all_names if n not in grouped]
    if other:
        groups.append(("Other", other))

    counts, total_rows = {}, 0
    for n in all_names:
        try:
            counts[n] = cur.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0]
        except Exception:
            counts[n] = None
        if counts[n]:
            total_rows += counts[n]

    size_mb = os.path.getsize(DB) / 1024 / 1024
    parts = []

    # ---------- nav ----------
    nav = []
    for title, names in groups:
        if not names:
            continue
        nav.append(f'<div class="navgrp">{html.escape(title)}</div>')
        for n in names:
            c = counts.get(n)
            nav.append(
                f'<a href="#{n}" class="navitem" data-name="{n}">'
                f'<span>{html.escape(n)}</span>'
                f'<span class="cnt">{c:,}</span></a>' if c is not None else
                f'<a href="#{n}" class="navitem" data-name="{n}">'
                f'<span>{html.escape(n)}</span><span class="cnt">—</span></a>')

    # ---------- tables ----------
    for title, names in groups:
        if not names:
            continue
        parts.append(f'<h2 class="grp">{html.escape(title)}</h2>')
        for n in names:
            cnt = counts.get(n)
            cols = cur.execute(f'PRAGMA table_info("{n}")').fetchall()
            colnames = [c["name"] for c in cols]
            schema = " · ".join(
                f'<span class="col">{html.escape(c["name"])}'
                f'<span class="ctype">{html.escape(c["type"] or "")}</span></span>'
                for c in cols)

            full = cnt is not None and cnt <= FULL_DUMP_UNDER
            limit = cnt if full else SAMPLE_ROWS
            try:
                rows = cur.execute(f'SELECT * FROM "{n}" LIMIT {int(limit or 0)}').fetchall()
            except Exception as e:
                rows = []
                schema += f'<div class="err">read error: {html.escape(str(e)[:120])}</div>'

            badge = ("full table" if full else
                     f"first {len(rows):,} of {cnt:,}") if cnt is not None else "n/a"
            parts.append(f"""
            <section id="{n}">
              <div class="thead">
                <h3>{html.escape(n)}<span class="kind">{kinds.get(n,'')}</span></h3>
                <div class="meta"><b>{(cnt if cnt is not None else 0):,}</b> rows
                  &nbsp;·&nbsp; {len(colnames)} cols
                  &nbsp;·&nbsp; <span class="badge">{badge}</span></div>
              </div>
              <div class="schema">{schema}</div>""")

            if not rows:
                parts.append('<div class="empty">no rows</div></section>')
                continue

            head = "".join(f"<th>{html.escape(c)}</th>" for c in colnames)
            body = "".join(
                "<tr>" + "".join(f"<td>{esc(r[c])}</td>" for c in colnames) + "</tr>"
                for r in rows)
            parts.append(
                f'<div class="wrap"><table><thead><tr>{head}</tr></thead>'
                f'<tbody>{body}</tbody></table></div></section>')

    con.close()

    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>axis.db — full database</title>
<style>
 *{{box-sizing:border-box}}
 body{{margin:0;font:13px/1.5 -apple-system,Segoe UI,Inter,sans-serif;
      background:#fcf8f8;color:#31263B;display:flex}}
 aside{{width:265px;flex:none;height:100vh;overflow:auto;position:sticky;top:0;
       background:#f9f1f1;border-right:1px solid #e4e0e3;padding:16px 0}}
 aside h1{{font-size:14px;margin:0 16px 3px;letter-spacing:-.02em}}
 aside .sub{{font-size:11px;color:#89828d;margin:0 16px 12px;
            font-family:ui-monospace,Consolas,monospace}}
 #filter{{width:calc(100% - 32px);margin:0 16px 12px;padding:6px 9px;font-size:12px;
         border:1px solid #cbc6cb;border-radius:3px;background:#fff}}
 .navgrp{{font:600 9.5px/1 ui-monospace,Consolas,monospace;letter-spacing:.13em;
         text-transform:uppercase;color:#89828d;margin:16px 16px 5px}}
 .navitem{{display:flex;justify-content:space-between;gap:8px;padding:4px 16px;
          color:#31263B;text-decoration:none;font-size:12px}}
 .navitem:hover{{background:#f4f1f2;color:#14141C}}
 .cnt{{font-family:ui-monospace,Consolas,monospace;font-size:10.5px;color:#89828d}}
 main{{flex:1;min-width:0;padding:22px 26px 90px}}
 header.top{{border-bottom:1px solid #e4e0e3;padding-bottom:14px;margin-bottom:6px}}
 header.top h1{{margin:0 0 4px;font-size:22px;letter-spacing:-.03em}}
 header.top .m{{font-family:ui-monospace,Consolas,monospace;font-size:11.5px;color:#89828d}}
 h2.grp{{font:600 10px/1 ui-monospace,Consolas,monospace;letter-spacing:.14em;
        text-transform:uppercase;color:#89828d;margin:34px 0 10px;
        padding-bottom:7px;border-bottom:1px solid #e4e0e3}}
 section{{background:#fff;border:1px solid #e4e0e3;border-radius:3px;
         margin-bottom:16px;overflow:hidden}}
 .thead{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
        padding:11px 14px;border-bottom:1px solid #e9e5e8;flex-wrap:wrap}}
 .thead h3{{margin:0;font-size:14px;letter-spacing:-.01em}}
 .kind{{font:400 9px/1 ui-monospace,Consolas,monospace;letter-spacing:.1em;
       text-transform:uppercase;color:#89828d;margin-left:8px;border:1px solid #e4e0e3;
       padding:2px 5px;border-radius:3px}}
 .meta{{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:#89828d}}
 .badge{{background:#f4f1f2;border:1px solid #e4e0e3;border-radius:3px;padding:1px 6px}}
 .schema{{padding:8px 14px;background:#fcfafb;border-bottom:1px solid #e9e5e8;
         font-family:ui-monospace,Consolas,monospace;font-size:10.5px;color:#5c5560}}
 .col{{white-space:nowrap}} .ctype{{color:#afa9b1;margin-left:4px}}
 .wrap{{overflow-x:auto;max-height:560px;overflow-y:auto}}
 table{{border-collapse:collapse;width:100%;font-size:11.5px}}
 th{{position:sticky;top:0;background:#f9f1f1;text-align:left;padding:7px 10px;
    border-bottom:1px solid #e4e0e3;font:600 10px/1.3 ui-monospace,Consolas,monospace;
    letter-spacing:.06em;text-transform:uppercase;color:#5c5560;white-space:nowrap;z-index:1}}
 td{{padding:6px 10px;border-bottom:1px solid #f2eef0;vertical-align:top;
    max-width:430px;word-break:break-word}}
 tr:hover td{{background:#fcfafb}}
 .null{{color:#c9c3c9;font-style:italic}}
 .empty,.err{{padding:14px;color:#89828d;font-size:12px}}
 .err{{color:#C4544F}}
</style></head><body>
<aside>
  <h1>axis.db</h1>
  <div class="sub">{len(all_names)} objects · {total_rows:,} rows · {size_mb:.1f} MB</div>
  <input id="filter" placeholder="Filter tables…" autocomplete="off">
  {''.join(nav)}
</aside>
<main>
  <header class="top">
    <h1>Axis Bank — complete database</h1>
    <div class="m">{html.escape(DB)}<br>
      {len(all_names)} tables &amp; views · {total_rows:,} total rows · {size_mb:.1f} MB ·
      generated {datetime.now().strftime('%d %b %Y %H:%M')}</div>
  </header>
  <p style="color:#89828d;font-size:12px;max-width:760px">
    Tables with {FULL_DUMP_UNDER} rows or fewer are shown in full; larger tables show the
    first {SAMPLE_ROWS} rows. Long cell values are truncated for readability. This page is a
    read-only snapshot — regenerate it after any new fetch or classification run.
  </p>
  {''.join(parts)}
</main>
<script>
 const f = document.getElementById('filter');
 f.addEventListener('input', () => {{
   const v = f.value.toLowerCase();
   document.querySelectorAll('.navitem').forEach(a => {{
     a.style.display = a.dataset.name.toLowerCase().includes(v) ? '' : 'none';
   }});
 }});
</script>
</body></html>"""

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(f"wrote {OUT}  ({os.path.getsize(OUT)/1024:.0f} KB)")
    print(f"{len(all_names)} objects · {total_rows:,} rows · db {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
