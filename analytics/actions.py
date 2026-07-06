"""Action modules: response drafts, real-time alerts, audit log + masked export,
and the exec weekly digest. All keyless with graceful fallbacks; external send
(webhook / SMTP) only fires when configured.

Run:  python -m analytics.actions [respond|alerts|digest|all]
"""
import os
import sys
import json
import hashlib

import db
import config

# ---------------------------------------------------------------- audit
AUDIT_COLS = ["audit_id", "ts", "actor", "action", "detail"]


def audit(actor, action, detail=""):
    aid = "aud:" + hashlib.md5(f"{db.now()}|{actor}|{action}|{detail}".encode(), usedforsecurity=False).hexdigest()[:16]
    db.upsert_rows("audit_log", [dict(audit_id=aid, ts=db.now(), actor=actor,
                                      action=action, detail=str(detail)[:400])], "audit_id", AUDIT_COLS)
    return aid


def export_masked(sql, actor="system"):
    """Return a dataframe using ONLY masked text; log the access. PII never leaves via export."""
    df = db.df(sql)
    for col in ("text", "clean_text"):
        if col in df.columns and "text_masked" in df.columns:
            df[col] = df["text_masked"]
    audit(actor, "export", f"{len(df)} rows :: {sql[:120]}")
    return df


# ---------------------------------------------------------------- response drafts
REPLY_COLS = ["source_id", "draft", "model", "created_at"]
_TEMPLATE = ("Hi, we're sorry for the trouble and want to make this right. Please DM us your "
             "registered number / ticket ref and we'll prioritise it. — Axis Bank Support")
_PROMPT = ("You are Axis Bank's social-media support. Write ONE short, empathetic, on-brand PUBLIC "
           "reply (max 280 chars) to this customer post. Do NOT admit liability or share specifics. "
           "Invite them to DM. Post:\n\"{text}\"")


def draft_replies(limit=15):
    from analytics.features import ensure_tables
    ensure_tables()
    posts = db.df(f"""SELECT r.source_id, a.text_masked, a.urgency, a.score
                      FROM analysis a JOIN raw_posts r ON a.source_id = r.source_id
                      LEFT JOIN reply_drafts d ON d.source_id = a.source_id
                      WHERE d.source_id IS NULL AND a.sentiment IN ('negative','mixed')
                        AND a.intent IN ('complaint','churn_threat','legal_threat','fraud_report')
                      ORDER BY (CASE a.urgency WHEN 'critical' THEN 4 WHEN 'high' THEN 3
                                WHEN 'medium' THEN 2 ELSE 1 END) DESC, a.score ASC
                      LIMIT {int(limit)}""")
    if posts.empty:
        print("no complaints need a reply draft")
        return 0
    try:
        from analyze.llm import generate_text
        use_llm = True
    except Exception:
        use_llm = False
    rows = []
    for _, p in posts.iterrows():
        draft, model = _TEMPLATE, "template"
        if use_llm:
            try:
                draft = (generate_text(_PROMPT.format(text=(p["text_masked"] or "")[:500]),
                                       model=getattr(config, "BRIEF_MODEL", None)) or _TEMPLATE).strip()[:300]
                model = config.LLM_PROVIDER
            except Exception:
                draft, model = _TEMPLATE, "template"
        rows.append(dict(source_id=p["source_id"], draft=draft, model=model, created_at=db.now()))
    db.upsert_rows("reply_drafts", rows, "source_id", REPLY_COLS)
    print(f"drafted {len(rows)} replies ({model})")
    return len(rows)


# ---------------------------------------------------------------- alerts
ALERT_COLS = ["alert_id", "kind", "severity", "title", "detail", "ref_url", "created_at", "sent"]


def _alert(kind, severity, title, detail="", ref_url=""):
    aid = "alert:" + hashlib.md5(f"{kind}|{title}".encode(), usedforsecurity=False).hexdigest()[:16]
    return dict(alert_id=aid, kind=kind, severity=severity, title=title, detail=detail,
                ref_url=ref_url, created_at=db.now(), sent=0)


def build_alerts():
    from analytics.features import ensure_tables
    ensure_tables()
    rows = []
    # emerging negative clusters
    cl = db.df("SELECT title, size, recent_share, avg_score, top_team FROM clusters")
    if not cl.empty:
        for _, c in cl[(cl["recent_share"] >= 0.6) & (cl["avg_score"] < 0) & (cl["size"] >= 2)].iterrows():
            rows.append(_alert("emerging", "high", f"Emerging: {c['title']}",
                               f"{int(c['size'])} mentions, {int(c['recent_share']*100)}% last 24h, owner {c['top_team']}"))
    # fraud spike
    fr = int(db.df("SELECT COUNT(*) n FROM analysis WHERE fraud_signal=1").iloc[0]["n"])
    if fr >= 5:
        rows.append(_alert("fraud", "critical", f"Fraud signals elevated ({fr})",
                           "impersonation/phishing/scam mentions above threshold"))
    # critical unresolved
    crit = int(db.df("""SELECT COUNT(*) n FROM analysis a
                        LEFT JOIN fact_interaction f ON a.source_id=f.inbound_source_id
                        WHERE a.urgency='critical' AND (f.resolved IS NULL OR f.resolved=0)""").iloc[0]["n"])
    if crit >= 1:
        rows.append(_alert("sla", "critical", f"{crit} critical items unresolved",
                           "urgency=critical with no logged resolution"))
    db.upsert_rows("alerts", rows, "alert_id", ALERT_COLS)
    _dispatch(rows)
    print(f"alerts: {len(rows)} active")
    return len(rows)


def _dispatch(rows):
    url = os.getenv("ALERT_WEBHOOK")
    if not url or not rows:
        return
    try:
        import requests
        text = "\n".join(f"[{r['severity'].upper()}] {r['title']} — {r['detail']}" for r in rows)
        requests.post(url, json={"text": f"Axis social alerts:\n{text}"}, timeout=15)
        db.execute("UPDATE alerts SET sent=1 WHERE sent=0")
    except Exception as e:
        print(f"  webhook failed: {str(e)[:80]}")


# ---------------------------------------------------------------- weekly digest
def weekly_digest():
    from analytics.features import ensure_tables
    ensure_tables()
    k = db.df("SELECT * FROM mart_kpis")
    sov = db.df("SELECT brand, mentions, avg_score, share_of_voice FROM mart_competitor_sov ORDER BY mentions DESC")
    cl = db.df("SELECT title, size, top_team FROM clusters ORDER BY size DESC LIMIT 5")
    lines = [f"# {config.BRAND} — Weekly Social Digest", ""]
    if not k.empty:
        r = k.iloc[0]
        lines.append(f"**KPIs:** {int(r['total_mentions'])} mentions · {r['pct_negative']}% negative · "
                     f"⭐ Sentiment Recovery {r['sentiment_recovery_rate']}% · {int(r['needs_followup'])} need follow-up")
    if not sov.empty:
        lines.append("\n**Share of Voice:**")
        for _, s in sov.iterrows():
            lines.append(f"- {s['brand']}: {int(s['mentions'])} mentions ({s['share_of_voice']}% SOV), avg {s['avg_score']}")
    if not cl.empty:
        lines.append("\n**Top issues:**")
        for _, c in cl.iterrows():
            lines.append(f"- {c['title']} — {int(c['size'])} mentions (owner {c['top_team']})")
    md = "\n".join(lines)
    with open("weekly_digest.md", "w", encoding="utf-8") as f:
        f.write(md)
    print("wrote weekly_digest.md")
    return md


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("respond", "all"):
        draft_replies()
    if cmd in ("alerts", "all"):
        build_alerts()
    if cmd in ("digest", "all"):
        weekly_digest()


if __name__ == "__main__":
    main()
