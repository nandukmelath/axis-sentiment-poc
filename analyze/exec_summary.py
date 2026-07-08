"""Auto exec brief: aggregate the analysis, then let Gemini write
'Top 5 issues this week + recommended action', board-ready.

Run:  python -m analyze.exec_summary
"""
import json
from collections import Counter
from config import BRAND, GEMINI_MODEL, BRIEF_MODEL
from db import init_db, df
from analyze.llm import generate_text


def aggregate():
    a = df("SELECT * FROM analysis")
    if a.empty:
        return None
    total = len(a)
    sent = a["sentiment"].value_counts().to_dict()
    neg_pct = round(100 * a["sentiment"].isin(["negative", "mixed"]).mean(), 1)
    complaints = int((a["intent"] == "complaint").sum())
    critical = int((a["urgency"] == "critical").sum())
    fraud = int(a["fraud_signal"].sum())
    churn = int(a["churn_risk"].sum())

    # negative sentiment per aspect
    aspect_neg = Counter()
    for js in a["aspects_json"].dropna():
        try:
            for it in json.loads(js):
                asp = it.get("aspect") if isinstance(it, dict) else None
                if asp and it.get("sentiment") in ("negative", "mixed"):
                    aspect_neg[asp] += 1
        except Exception:
            pass

    clusters = df("SELECT title, size, avg_score, top_team, recent_share FROM clusters ORDER BY size DESC LIMIT 8")
    top_neg = df("""SELECT summary, urgency, recommended_team FROM analysis
                    WHERE score < 0 ORDER BY score ASC, urgency DESC LIMIT 12""")
    # defense-in-depth: these summaries feed a third-party LLM prompt — mask any PII that
    # slipped through (idempotent; the cascade already masks at source).
    if not top_neg.empty:
        from analyze.pii import mask as _pii_mask
        top_neg["summary"] = top_neg["summary"].map(lambda s: _pii_mask(s or "")[0])

    return {
        "brand": BRAND, "total": total, "sentiment": sent, "neg_pct": neg_pct,
        "complaints": complaints, "critical": critical, "fraud": fraud, "churn": churn,
        "aspect_negatives": aspect_neg.most_common(8),
        "top_clusters": clusters.to_dict("records"),
        "worst_posts": top_neg.to_dict("records"),
    }


PROMPT = """You are briefing Axis Bank leadership. Using ONLY the aggregated data below, write a
tight executive brief in markdown with these sections:
1. **Headline** — one sentence on overall sentiment health.
2. **Top 5 issues** — for each: the issue, rough volume, and a specific recommended action + owning team.
3. **Risk watch** — fraud signals, critical/urgent items, churn threats.
4. **One recommendation** — the single most important move this week.
Be concrete and numeric. Do not invent data.

DATA (JSON):
{data}"""


def _template_brief(agg):
    """KEYLESS deterministic brief from the aggregates — used when no LLM key is set."""
    lines = [f"# {agg['brand']} — Social Sentiment Brief (auto, keyless)", ""]
    lines.append(f"**Headline:** {agg['total']} mentions analysed · {agg['neg_pct']}% negative · "
                 f"{agg['complaints']} complaints · {agg['critical']} critical · {agg['fraud']} fraud · "
                 f"{agg['churn']} churn-risk.")
    lines.append("\n**Top issues (by volume):**")
    for c in (agg["top_clusters"] or [])[:5]:
        lines.append(f"- {c.get('title','(issue)')} — {int(c.get('size',0))} mentions, "
                     f"avg {c.get('avg_score')}, owner **{c.get('top_team','none')}**"
                     + (" · 🚨 emerging" if (c.get('recent_share') or 0) >= 0.6 and (c.get('avg_score') or 0) < 0 else ""))
    if agg["aspect_negatives"]:
        lines.append("\n**Where it hurts (negative by aspect):** " +
                     ", ".join(f"{a} ({n})" for a, n in agg["aspect_negatives"]))
    lines.append(f"\n**Risk watch:** {agg['fraud']} fraud signals · {agg['critical']} critical · "
                 f"{agg['churn']} churn threats.")
    lines.append("\n**Recommendation:** act on the largest emerging negative cluster first; "
                 "route each issue to its owning team; monitor the Sentiment Recovery Rate.")
    lines.append("\n_(No LLM key set — generated deterministically. Set LLM_PROVIDER + key for a narrative brief.)_")
    return "\n".join(lines)


def main():
    init_db()
    agg = aggregate()
    if not agg:
        print("no analysis yet — run analyze.run_analyze first.")
        return
    try:
        md = generate_text(PROMPT.format(data=json.dumps(agg, ensure_ascii=False, default=str)),
                            model=BRIEF_MODEL)
    except Exception as e:
        print(f"LLM unavailable ({str(e)[:60]}) — writing keyless template brief.")
        md = _template_brief(agg)
    out = "exec_summary.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print(md[:400])
    print(f"\n(saved to {out})")


if __name__ == "__main__":
    main()
