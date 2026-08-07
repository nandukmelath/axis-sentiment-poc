"""Axis Sentiment — one MCP server for the whole system.

Exposes the entire stack (DB, warehouse marts, ingestion, LLM classification,
clustering, briefs, pipeline runs) as MCP tools so any MCP client can drive it.

Run (stdio):        python mcp_server.py
Register with Claude Code:
    claude mcp add axis -- python C:/Users/nandu/axis-sentiment-poc/mcp_server.py

Env:
    AXIS_MCP_READONLY=1   disable every action/mutating tool (query tools stay on)
    AXIS_MCP_TIMEOUT=300  default seconds for subprocess actions
    (DATABASE_URL / GROQ_API_KEY / SCRAPEBADGER_API_KEY etc. are read from .env)
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from typing import Any, Optional

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import pandas as pd  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

import config  # noqa: E402  (loads .env)
import db  # noqa: E402

mcp = FastMCP("axis-sentiment")

READONLY = os.getenv("AXIS_MCP_READONLY", "").lower() in ("1", "true", "yes")
DEFAULT_TIMEOUT = int(os.getenv("AXIS_MCP_TIMEOUT", "300"))
MAX_ROWS = 500


# ---------------------------------------------------------------- helpers
def _clean(v: Any) -> Any:
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, "item"):          # numpy scalar
        try:
            return v.item()
        except Exception:
            return str(v)
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return v


def _records(df: pd.DataFrame, limit: int = MAX_ROWS) -> list[dict]:
    if df is None or df.empty:
        return []
    out = df.head(limit).to_dict("records")
    return [{k: _clean(v) for k, v in r.items()} for r in out]


def _q(sql: str, limit: int = MAX_ROWS) -> list[dict]:
    try:
        return _records(db.df(sql), limit)
    except Exception as e:
        return [{"error": str(e)[:300], "sql": sql[:200]}]


def _tables() -> list[str]:
    if db.DIALECT == "sqlite":
        q = ("SELECT name FROM sqlite_master WHERE type IN ('table','view') "
             "AND name NOT LIKE 'sqlite_%' ORDER BY name")
    else:
        q = ("SELECT table_name AS name FROM information_schema.tables "
             "WHERE table_schema='public' ORDER BY table_name")
    try:
        return db.df(q)["name"].tolist()
    except Exception:
        return []


def _guard_action() -> Optional[str]:
    if READONLY:
        return "BLOCKED: AXIS_MCP_READONLY=1 — action tools are disabled on this server."
    return None


def _run(module_args: list[str], timeout: int | None = None) -> dict:
    """Run a project module as a subprocess and return a compact result."""
    timeout = timeout or DEFAULT_TIMEOUT
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        p = subprocess.run([sys.executable, "-m", *module_args], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=timeout,
                           errors="replace")
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {timeout}s",
                "hint": "raise the timeout arg, or run this module from a terminal"}
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    return {"ok": p.returncode == 0, "exit_code": p.returncode,
            "output_tail": out[-2500:] if out else "(no output)"}


# ================================================================ QUERY TOOLS
@mcp.tool()
def system_status() -> dict:
    """Health snapshot: DB target, row counts, configured providers, latest run.

    Start here — tells you what data exists and which integrations are keyed."""
    counts = {}
    for t in ("raw_posts", "analysis", "clusters", "clean_posts", "fact_mention"):
        try:
            counts[t] = int(db.df(f"SELECT count(*) c FROM {t}").c.iloc[0])
        except Exception:
            counts[t] = None
    span = _q("SELECT min(created_at) AS earliest, max(created_at) AS latest "
              "FROM raw_posts WHERE created_at LIKE '20%'", 1)
    providers = {
        "llm_provider": getattr(config, "LLM_PROVIDER", None),
        "gemini": bool(os.getenv("GEMINI_API_KEY")),
        "groq": bool(os.getenv("GROQ_API_KEY")),
        "openai": bool(os.getenv("OPENAI_API_KEY")),
        "scrapebadger": bool(os.getenv("SCRAPEBADGER_API_KEY")),
        "reddit": bool(os.getenv("REDDIT_CLIENT_ID")),
        "youtube": bool(os.getenv("YOUTUBE_API_KEY")),
    }
    return {
        "database": {"dialect": db.DIALECT, "url": re.sub(r"//[^@]+@", "//***@", db.DB_URL)},
        "counts": counts,
        "date_span": span[0] if span else {},
        "providers": providers,
        "readonly_mode": READONLY,
        "tables": len(_tables()),
    }


@mcp.tool()
def describe_schema(table: str = "") -> dict:
    """List all tables/views, or the columns of one table.

    Args:
        table: optional table/view name. Omit to list everything.
    """
    names = _tables()
    if not table:
        return {"count": len(names), "tables": names,
                "hint": "call describe_schema('scored_posts') for columns"}
    if table not in names:
        return {"error": f"unknown table '{table}'", "available": names}
    if db.DIALECT == "sqlite":
        cols = _q(f"PRAGMA table_info({table})")
        cols = [{"name": c.get("name"), "type": c.get("type")} for c in cols]
    else:
        cols = _q("SELECT column_name AS name, data_type AS type FROM information_schema.columns "
                  f"WHERE table_name='{table}' ORDER BY ordinal_position")
    n = _q(f"SELECT count(*) c FROM {table}", 1)
    return {"table": table, "rows": (n[0]["c"] if n else None), "columns": cols}


@mcp.tool()
def search_mentions(query: str = "", source: str = "", sentiment: str = "",
                    urgency: str = "", team: str = "", since: str = "",
                    fraud_only: bool = False, churn_only: bool = False,
                    limit: int = 50) -> dict:
    """Search analysed social mentions (post text + date + author + AI sentiment).

    Reads the `scored_posts` view — raw post joined to its classification.

    Args:
        query: substring to find in the post text
        source: twitter | reddit | play | news | youtube | appstore | ...
        sentiment: positive | negative | neutral | mixed
        urgency: critical | high | medium | low
        team: owning team, e.g. payments_upi, fraud_cyber, retention
        since: ISO date lower bound, e.g. "2026-06-01"
        fraud_only / churn_only: keep only flagged rows
        limit: max rows (<=500)
    """
    w, p = [], {}
    if query:
        p["q"] = f"%{query}%"
        w.append("text LIKE :q")
    for col, val in (("source", source), ("sentiment", sentiment),
                     ("urgency", urgency), ("recommended_team", team)):
        if val:
            p[col] = val
            w.append(f"{col} = :{col}")
    if since:
        p["since"] = since
        w.append("created_at >= :since")
    if fraud_only:
        w.append("fraud_signal = 1")
    if churn_only:
        w.append("churn_risk = 1")
    where = ("WHERE " + " AND ".join(w)) if w else ""
    sql = (f"SELECT created_at, source, author, sentiment, score, urgency, intent, "
           f"recommended_team, fraud_signal, churn_risk, summary, text, url "
           f"FROM scored_posts {where} ORDER BY created_at DESC LIMIT {min(int(limit), MAX_ROWS)}")
    try:
        rows = _records(db.df(sql, p) if p else db.df(sql), limit)
    except TypeError:              # db.df without params support
        rows = _q(sql.replace(":q", f"'%{query}%'"), limit)
    except Exception as e:
        return {"error": str(e)[:300]}
    return {"count": len(rows), "filters_applied": {k: v for k, v in
            dict(query=query, source=source, sentiment=sentiment, urgency=urgency,
                 team=team, since=since, fraud_only=fraud_only, churn_only=churn_only).items() if v},
            "mentions": rows}


@mcp.tool()
def get_kpis() -> dict:
    """Headline metrics: volume, net sentiment, %negative, complaints, critical, fraud, churn."""
    base = _q("""SELECT count(*) AS mentions,
                        AVG(score) AS net_sentiment,
                        SUM(CASE WHEN sentiment IN ('negative','mixed') THEN 1 ELSE 0 END) AS negative,
                        SUM(CASE WHEN intent='complaint' THEN 1 ELSE 0 END) AS complaints,
                        SUM(CASE WHEN urgency='critical' THEN 1 ELSE 0 END) AS critical,
                        SUM(fraud_signal) AS fraud_flags,
                        SUM(churn_risk) AS churn_flags
                 FROM analysis""", 1)
    k = base[0] if base else {}
    if k.get("mentions"):
        k["pct_negative"] = round(100 * (k.get("negative") or 0) / k["mentions"], 1)
        if k.get("net_sentiment") is not None:
            k["net_sentiment"] = round(k["net_sentiment"], 3)
    k["by_source"] = _q("SELECT source, count(*) n FROM raw_posts GROUP BY source ORDER BY n DESC", 30)
    k["by_sentiment"] = _q("SELECT sentiment, count(*) n FROM analysis GROUP BY sentiment ORDER BY n DESC", 10)
    k["by_urgency"] = _q("SELECT urgency, count(*) n FROM analysis GROUP BY urgency ORDER BY n DESC", 10)
    return k


@mcp.tool()
def get_issues(emerging_only: bool = False, limit: int = 25) -> dict:
    """Auto-clustered issues (dedup'd themes), with the 24h emerging-spike flag.

    Args:
        emerging_only: only issues spiking in the last 24h with negative sentiment
        limit: max issues
    """
    rows = _q("SELECT cluster_id, title, size, recent_share, avg_score, top_team "
              "FROM clusters ORDER BY size DESC", limit)
    for r in rows:
        r["emerging"] = bool((r.get("recent_share") or 0) >= 0.6
                             and (r.get("avg_score") or 0) < 0 and (r.get("size") or 0) >= 2)
    if emerging_only:
        rows = [r for r in rows if r["emerging"]]
    return {"count": len(rows), "issues": rows}


@mcp.tool()
def priority_queue(limit: int = 20) -> dict:
    """The ranked action list: what a bank ops team should handle first.

    Scored by urgency x reach x negativity — each row carries the owning team,
    the recommended action, and a link to the original post."""
    rows = _q("""SELECT urgency, sentiment, score, recommended_team, recommended_action,
                        summary, author, source, created_at, url, engagement, fraud_signal
                 FROM scored_posts
                 WHERE sentiment IN ('negative','mixed') OR urgency IN ('critical','high')""", 400)
    w = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    for r in rows:
        eng = r.get("engagement") or 0
        neg = max(0.0, -(r.get("score") or 0))
        r["priority"] = round(w.get(r.get("urgency"), 1) * (1 + eng ** 0.5) * (0.5 + neg), 2)
    rows.sort(key=lambda r: -r["priority"])
    return {"count": min(len(rows), limit), "queue": rows[:limit]}


@mcp.tool()
def get_mart(name: str = "", limit: int = 100) -> dict:
    """Read any warehouse mart / fact / dim table by name.

    Covers the whole gold layer in one tool — competitor SOV, channels, products,
    trends, geo, influencers, team queues, fraud, churn, forecast, entities,
    RM enablement, admin analytics, KPIs, facts and dimensions.

    Args:
        name: table name. Omit to list available marts.
        limit: max rows
    """
    names = _tables()
    marts = [n for n in names if n.startswith(("mart_", "fact_", "dim_", "vw_"))]
    if not name:
        return {"available": marts, "hint": "get_mart('mart_competitor_sov')"}
    if name not in names:
        return {"error": f"unknown table '{name}'", "available": marts}
    return {"table": name, "rows": _q(f"SELECT * FROM {name}", limit)}


_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|replace|attach|detach|"
    r"pragma|vacuum|grant|revoke|copy)\b", re.I)


@mcp.tool()
def run_sql(sql: str, limit: int = 200) -> dict:
    """Run a READ-ONLY SQL SELECT against the database.

    Only a single SELECT/WITH statement is allowed; any write/DDL keyword or
    statement chaining is rejected. Use describe_schema() to discover tables.

    Args:
        sql: the SELECT statement
        limit: row cap applied to the result
    """
    s = (sql or "").strip().rstrip(";")
    if not s:
        return {"error": "empty sql"}
    if ";" in s:
        return {"error": "multiple statements are not allowed"}
    if not re.match(r"^\s*(select|with)\b", s, re.I):
        return {"error": "only SELECT/WITH queries are allowed"}
    if _FORBIDDEN.search(s):
        return {"error": "query contains a forbidden (write/DDL) keyword"}
    rows = _q(s, limit)
    return {"row_count": len(rows), "rows": rows}


@mcp.tool()
def get_exec_brief() -> dict:
    """The latest AI-written executive brief (top issues + recommended actions)."""
    for fn in ("exec_summary.md", "weekly_digest.md"):
        p = os.path.join(ROOT, fn)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return {"file": fn, "markdown": f.read()}
    return {"error": "no brief found — run generate_exec_brief()"}


# ================================================================ ACTION TOOLS
@mcp.tool()
def fetch_sources(source: str = "", timeout: int = 0) -> dict:
    """Pull fresh mentions from the ingestion sources into raw_posts.

    Args:
        source: one of news, play, appstore, reddit, youtube, scrapebadger, twitter,
                hackernews, mastodon, technofino, rssnews, gdelt, tiktok,
                consumercomplaints, trustpilot, linkedin, instagram, facebook,
                mouthshut, googlereviews.
                Omit to run every source. NOTE: 'scrapebadger' and the ScrapeBadger-backed
                sources (tiktok, linkedin, instagram, facebook, consumercomplaints,
                trustpilot, mouthshut, googlereviews) spend paid API credits.
        timeout: seconds (default AXIS_MCP_TIMEOUT)
    """
    if (b := _guard_action()):
        return {"ok": False, "error": b}
    args = ["fetch.run_fetch"] + (["--only", source] if source else [])
    return _run(args, timeout or None)


@mcp.tool()
def classify(phase: str = "both", limit: int = 0, timeout: int = 0) -> dict:
    """Score unanalysed posts: VADER baseline (free) + LLM depth on negatives.

    Args:
        phase: baseline (free, instant) | llm (uses LLM_PROVIDER credits) | both
        limit: max posts this run (0 = no cap)
        timeout: seconds
    """
    if (b := _guard_action()):
        return {"ok": False, "error": b}
    if phase not in ("baseline", "llm", "both"):
        return {"ok": False, "error": "phase must be baseline | llm | both"}
    args = ["analyze.run_analyze", "--phase", phase]
    if limit:
        args += ["--limit", str(int(limit))]
    return _run(args, timeout or None)


@mcp.tool()
def rebuild_clusters(timeout: int = 0) -> dict:
    """Re-embed posts and rebuild the issue clusters + emerging-spike flags."""
    if (b := _guard_action()):
        return {"ok": False, "error": b}
    return _run(["analyze.embed_cluster"], timeout or None)


@mcp.tool()
def generate_exec_brief(timeout: int = 0) -> dict:
    """Ask the LLM to write a fresh executive brief from current data."""
    if (b := _guard_action()):
        return {"ok": False, "error": b}
    r = _run(["analyze.exec_summary"], timeout or None)
    if r.get("ok"):
        r["brief"] = get_exec_brief().get("markdown", "")[:4000]
    return r


@mcp.tool()
def build_warehouse(step: str = "", timeout: int = 0) -> dict:
    """Rebuild the warehouse gold layer (dims, facts, resolution, marts).

    Args:
        step: optional single step, e.g. 'star'. Omit to build everything.
    """
    if (b := _guard_action()):
        return {"ok": False, "error": b}
    args = ["warehouse.build"] + (["--step", step] if step else [])
    return _run(args, timeout or None)


@mcp.tool()
def run_pipeline(window: str = "", timeout: int = 0) -> dict:
    """Run the full end-to-end pipeline: fetch -> classify -> cluster -> marts -> brief.

    Args:
        window: 1h | 1d | 1m to scope the fetch; omit for the default window
        timeout: seconds (this is the long one — 600+ recommended)
    """
    if (b := _guard_action()):
        return {"ok": False, "error": b}
    args = ["run_window"] + (["--window", window] if window else [])
    return _run(args, timeout or 600)


@mcp.tool()
def backfill_x(days: int = 30, window: int = 7, pages: int = 5,
               query: str = "", timeout: int = 0) -> dict:
    """Historical X/Twitter backfill via the ScrapeBadger API.

    COSTS MONEY — each window burns paid API credits. Start small (days=30).

    Args:
        days: how far back to go
        window: days per search window
        pages: pages per window (100 tweets/page)
        query: override the search query
        timeout: seconds
    """
    if (b := _guard_action()):
        return {"ok": False, "error": b}
    if not os.getenv("SCRAPEBADGER_API_KEY"):
        return {"ok": False, "error": "SCRAPEBADGER_API_KEY not set"}
    args = ["fetch.scrapebadger", "backfill", "--days", str(int(days)),
            "--window", str(int(window)), "--pages", str(int(pages))]
    if query:
        args += ["--query", query]
    return _run(args, timeout or 900)


# ================================================================ RESOURCES
@mcp.resource("axis://status")
def res_status() -> str:
    """Live system status as JSON."""
    return json.dumps(system_status(), indent=2, default=str)


@mcp.resource("axis://schema")
def res_schema() -> str:
    """All tables and views in the database."""
    return json.dumps(describe_schema(), indent=2, default=str)


@mcp.resource("axis://exec-brief")
def res_brief() -> str:
    """The latest executive brief (markdown)."""
    return get_exec_brief().get("markdown", "No brief generated yet.")


@mcp.resource("axis://kpis")
def res_kpis() -> str:
    """Current headline KPIs as JSON."""
    return json.dumps(get_kpis(), indent=2, default=str)


if __name__ == "__main__":
    mcp.run()
