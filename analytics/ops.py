"""Tier-4 ops: model-quality tracking (drift) + LLM cost estimate. All keyless.

Run:  python -m analytics.ops
"""
import json
import db
import config
from analytics.features import ensure_tables, EVAL_COLS, RUNM_COLS

# per-MILLION-token USD (illustrative — confirm against the provider's pricing page).
PRICE = {
    "groq": (0.0, 0.0), "llama-3.3-70b-versatile": (0.0, 0.0), "llama-3.1-8b-instant": (0.0, 0.0),
    "gemini-2.5-flash": (0.30, 2.50), "gemini-2.5-flash-lite": (0.10, 0.40),
}
TOK_IN, TOK_OUT = 300, 200        # rough tokens per classified post


def record_quality():
    """Log model-quality proxies over time so drift is visible (no LLM cost)."""
    ensure_tables()
    a = db.df("SELECT model, confidence FROM analysis")
    if a.empty:
        return {}
    ts = db.now()
    conf = a["confidence"].fillna(0)
    rows = [
        {"run_ts": ts, "metric": "llm_coverage_pct", "value": round(100 * (a["model"] != "vader-fast").mean(), 2)},
        {"run_ts": ts, "metric": "avg_confidence", "value": round(float(conf.mean()), 3)},
        {"run_ts": ts, "metric": "high_conf_pct", "value": round(100 * (conf >= 0.5).mean(), 2)},
    ]
    db.insert_rows("eval_history", rows, EVAL_COLS)
    return {r["metric"]: r["value"] for r in rows}


def drift_flags():
    """Compare the latest quality snapshot to the previous one; flag drops."""
    h = db.df("SELECT * FROM eval_history ORDER BY run_ts")
    flags = []
    if h.empty:
        return flags
    for metric, g in h.groupby("metric"):
        g = g.sort_values("run_ts")
        if len(g) >= 2:
            prev, cur = float(g.iloc[-2]["value"]), float(g.iloc[-1]["value"])
            if prev and (prev - cur) / abs(prev) > 0.15:
                flags.append({"metric": metric, "prev": prev, "cur": cur})
    return flags


def estimate_cost():
    ensure_tables()
    a = db.df("SELECT model, COUNT(*) n FROM analysis GROUP BY model")
    total_tokens, total_cost, llm_calls = 0, 0.0, 0
    for _, r in a.iterrows():
        if r["model"] == "vader-fast":
            continue
        n = int(r["n"])
        llm_calls += n
        pin, pout = PRICE.get(r["model"], (0.0, 0.0))
        total_tokens += n * (TOK_IN + TOK_OUT)
        total_cost += n * TOK_IN / 1e6 * pin + n * TOK_OUT / 1e6 * pout
    mentions = int(db.df("SELECT COUNT(*) n FROM analysis").iloc[0]["n"])
    row = dict(run_ts=db.now(), mentions=mentions, llm_calls=llm_calls, tokens_est=int(total_tokens),
               cost_usd_est=round(total_cost, 4), provider=config.LLM_PROVIDER)
    db.upsert_rows("run_metrics", [row], "run_ts", RUNM_COLS)
    return row


def run_all():
    q = record_quality()
    c = estimate_cost()
    print("ops:", json.dumps({"quality": q, "cost": c}, default=str))
    return {"quality": q, "cost": c}


if __name__ == "__main__":
    run_all()
