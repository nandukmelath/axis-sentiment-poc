"""Dashboard panels for the product features. Each reads its mart and renders.
Imported by dashboard/app.py and wired into role-based tabs."""
import os
import pandas as pd
import plotly.express as px
import streamlit as st

import db

SENT_COLORS = {"negative": "#C0392B", "positive": "#2E8B57", "neutral": "#7F8C8D", "mixed": "#E67E22"}


def _safe(sql, params=None):
    try:
        return db.df(sql, params) if params else db.df(sql)
    except Exception:
        return pd.DataFrame()


def _empty(msg):
    st.info(msg)


# ---------------------------------------------------------------- Competitor SOV
def competitor_sov():
    st.subheader("🏁 Competitor Share-of-Voice")
    st.caption("Axis vs HDFC · ICICI · SBI · Kotak — volume + sentiment (keyless news).")
    m = _safe("SELECT * FROM mart_competitor_sov ORDER BY mentions DESC")
    if m.empty:
        return _empty("Run `python -m analytics.competitor`.")
    c = st.columns([0.5, 0.5])
    with c[0]:
        fig = px.bar(m, x="brand", y="share_of_voice", color="brand", text="share_of_voice")
        fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=320, showlegend=False,
                          yaxis_title="% share of voice", xaxis_title="")
        st.plotly_chart(fig, width="stretch")
    with c[1]:
        fig2 = px.bar(m, x="brand", y="avg_score", color="avg_score",
                      color_continuous_scale=["#C0392B", "#7F8C8D", "#2E8B57"], range_color=[-1, 1])
        fig2.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=320, xaxis_title="",
                           yaxis_title="avg sentiment")
        st.plotly_chart(fig2, width="stretch")
    st.dataframe(m[["brand", "mentions", "share_of_voice", "pct_negative", "avg_score"]],
                 width="stretch", hide_index=True)


# ---------------------------------------------------------------- Product scorecards
def product_scorecards():
    st.subheader("📦 Product Scorecards")
    st.caption("Per-product sentiment, complaints, and an NPS-proxy.")
    m = _safe("SELECT * FROM mart_product_scorecard ORDER BY mentions DESC")
    if m.empty:
        return _empty("Run `python -m analytics.features`.")
    top = m.head(15)
    fig = px.bar(top, x="nps_proxy", y="product", orientation="h", color="nps_proxy",
                 color_continuous_scale=["#C0392B", "#7F8C8D", "#2E8B57"], range_color=[-100, 100])
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=460, yaxis_title="",
                      xaxis_title="NPS-proxy")
    st.plotly_chart(fig, width="stretch")
    st.dataframe(m[["product", "mentions", "pct_negative", "complaints", "avg_score", "nps_proxy"]],
                 width="stretch", hide_index=True)


# ---------------------------------------------------------------- Trends / anomaly
def trends_panel():
    st.subheader("📈 Trends & Anomaly Detection")
    st.caption("Daily volume per RBI category, with spike (z≥2 on negative) flagged.")
    m = _safe("SELECT * FROM mart_trends ORDER BY day")
    if m.empty:
        return _empty("Run `python -m analytics.features`.")
    anom = m[m["anomaly"] == 1]
    if not anom.empty:
        for _, a in anom.iterrows():
            st.error(f"🚨 Spike — **{a['category']}** on {a['day']}: {int(a['mentions'])} mentions "
                     f"(z={a['z_score']}), avg sentiment {a['avg_score']}")
    fig = px.line(m, x="day", y="mentions", color="category", markers=True)
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=380, xaxis_title="", legend_title="")
    st.plotly_chart(fig, width="stretch")


# ---------------------------------------------------------------- Geo heatmap
def geo_panel():
    st.subheader("🗺️ Geo Sentiment")
    st.caption("Sentiment by city (inferred from post text).")
    m = _safe("SELECT * FROM mart_geo ORDER BY mentions DESC")
    if m.empty:
        return _empty("Run `python -m analytics.features`. (No city mentions detected yet.)")
    fig = px.bar(m, x="city", y="mentions", color="avg_score",
                 color_continuous_scale=["#C0392B", "#7F8C8D", "#2E8B57"], range_color=[-1, 1],
                 hover_data=["region", "pct_negative"])
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=360, xaxis_title="", yaxis_title="mentions")
    st.plotly_chart(fig, width="stretch")
    reg = m.groupby("region").agg(mentions=("mentions", "sum"), avg=("avg_score", "mean")).reset_index()
    st.dataframe(reg, width="stretch", hide_index=True)


# ---------------------------------------------------------------- Influencer watch
def influencers_panel():
    st.subheader("📢 Influencer / Journalist Watch")
    st.caption("High-reach authors and their stance — handle these first.")
    m = _safe("SELECT * FROM mart_influencers ORDER BY reach DESC")
    if m.empty:
        return _empty("Run `python -m analytics.features`.")
    neg = m[m["stance"] == "negative"]
    if not neg.empty:
        st.warning(f"⚠️ {len(neg)} high-reach authors are negative — priority outreach.")
    st.dataframe(m[["author", "author_name", "reach", "mentions", "stance", "avg_score", "worst_summary", "url"]],
                 width="stretch", hide_index=True,
                 column_config={"url": st.column_config.LinkColumn("link", display_text="open")})


# ---------------------------------------------------------------- Team queues (auto-routing)
def team_queues():
    st.subheader("🧭 Team Work-Queues (auto-routed)")
    st.caption("Every issue routed to its owning team. Open = needs follow-up.")
    m = _safe("SELECT * FROM mart_team_queue WHERE team <> 'none' ORDER BY open_items DESC")
    if m.empty:
        return _empty("Run `python -m analytics.features`.")
    k = st.columns(4)
    k[0].metric("Teams with work", int((m["open_items"] > 0).sum()))
    k[1].metric("Open items", int(m["open_items"].sum()))
    k[2].metric("🔴 Critical", int(m["critical"].sum()))
    k[3].metric("🛡️ Fraud", int(m["fraud"].sum()))
    fig = px.bar(m, x="open_items", y="team", orientation="h", color="critical",
                 color_continuous_scale="Reds")
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=360, yaxis_title="", xaxis_title="open items")
    st.plotly_chart(fig, width="stretch")
    team = st.selectbox("Open a team's worklist", m["team"].tolist(), key="tq_team")
    q = _safe("""SELECT r.created_at, a.urgency, a.summary, a.recommended_action, r.url
                 FROM analysis a JOIN raw_posts r ON a.source_id=r.source_id
                 WHERE a.recommended_team = :t AND (a.intent IN ('complaint','churn_threat','legal_threat','fraud_report')
                       OR a.urgency IN ('high','critical'))
                 ORDER BY (CASE a.urgency WHEN 'critical' THEN 4 WHEN 'high' THEN 3 ELSE 2 END) DESC LIMIT 40""",
              {"t": team})
    st.dataframe(q, width="stretch", hide_index=True,
                 column_config={"url": st.column_config.LinkColumn("link", display_text="open")})


# ---------------------------------------------------------------- Fraud board
def fraud_board():
    st.subheader("🛡️ Fraud Early-Warning Board")
    m = _safe("SELECT * FROM mart_fraud ORDER BY cnt DESC")
    if m.empty:
        return _empty("Run `python -m analytics.features`.")
    st.metric("Fraud-flagged mentions", int(m["cnt"].sum()))
    st.dataframe(m[["fraud_type", "cnt", "sample_handles", "avg_score", "sample_url"]],
                 width="stretch", hide_index=True,
                 column_config={"sample_url": st.column_config.LinkColumn("link", display_text="open")})
    st.caption("Recent fraud-flagged posts")
    q = _safe("""SELECT r.created_at, r.author, a.fraud_type, a.summary, r.url
                 FROM analysis a JOIN raw_posts r ON a.source_id=r.source_id
                 WHERE a.fraud_signal=1 ORDER BY r.created_at DESC LIMIT 30""")
    st.dataframe(q, width="stretch", hide_index=True,
                 column_config={"url": st.column_config.LinkColumn("link", display_text="open")})


# ---------------------------------------------------------------- Alerts
def alerts_panel():
    st.subheader("🔔 Real-time Alerts")
    m = _safe("SELECT * FROM alerts ORDER BY created_at DESC")
    if m.empty:
        return _empty("No active alerts. Run `python -m analytics.actions alerts`.")
    for _, a in m.iterrows():
        fn = st.error if a["severity"] == "critical" else st.warning
        sent = " · 📨 sent" if int(a.get("sent", 0) or 0) else ""
        fn(f"**[{a['severity'].upper()}] {a['title']}** — {a['detail']}{sent}")
    st.caption("Set ALERT_WEBHOOK in .env to push these to Slack/Teams.")


# ---------------------------------------------------------------- Response drafts
def response_drafts():
    st.subheader("✍️ Response-Draft Assistant")
    st.caption("Suggested on-brand public replies for the top complaints — edit before posting.")
    m = _safe("""SELECT d.source_id, a.urgency, r.author, a.text_masked AS complaint, d.draft, d.model, r.url
                 FROM reply_drafts d JOIN raw_posts r ON d.source_id=r.source_id
                 JOIN analysis a ON a.source_id=d.source_id
                 ORDER BY (CASE a.urgency WHEN 'critical' THEN 4 WHEN 'high' THEN 3 ELSE 2 END) DESC""")
    if m.empty:
        return _empty("Run `python -m analytics.actions respond`.")
    for _, r in m.head(20).iterrows():
        with st.expander(f"[{r['urgency']}] {r['author']} — {str(r['complaint'])[:80]}"):
            st.markdown(f"**Complaint:** {r['complaint']}")
            st.text_area("Suggested reply", value=r["draft"], key=f"d_{r['source_id']}", height=90)
            st.caption(f"model: {r['model']} · {r['url']}")


# ---------------------------------------------------------------- Weekly digest
def weekly_digest_panel():
    st.subheader("🗞️ Exec Weekly Digest")
    if os.path.exists("weekly_digest.md"):
        st.markdown(open("weekly_digest.md", encoding="utf-8").read())
    elif os.path.exists("exec_summary.md"):
        st.markdown(open("exec_summary.md", encoding="utf-8").read())
    else:
        _empty("Run `python -m analytics.actions digest`.")


# ---------------------------------------------------------------- Root-cause drill-down
def root_cause_panel():
    st.subheader("🔬 Root-cause Drill-down")
    st.caption("Pick an issue cluster → representative posts → suggested fix.")
    cl = _safe("SELECT * FROM clusters ORDER BY size DESC")
    if cl.empty:
        return _empty("Run `python -m analyze.embed_cluster`.")
    pick = st.selectbox("Issue", cl["title"].tolist(), key="rc_cluster")
    cid = int(cl[cl["title"] == pick].iloc[0]["cluster_id"])
    posts = _safe("""SELECT r.created_at, r.author, a.root_cause, a.recommended_action,
                            a.recommended_team, a.summary, r.url
                     FROM analysis a JOIN raw_posts r ON a.source_id=r.source_id
                     WHERE a.cluster_id = :c ORDER BY a.score ASC LIMIT 25""", {"c": cid})
    rc = posts["root_cause"].dropna()
    rc = rc[rc != ""]
    if not rc.empty:
        st.markdown(f"**Most common root cause:** {rc.mode().iloc[0]}")
    act = posts["recommended_action"].dropna()
    act = act[act != ""]
    if not act.empty:
        st.success(f"**Suggested fix:** {act.mode().iloc[0]}  ·  owner: {posts['recommended_team'].mode().iloc[0]}")
    st.dataframe(posts[["created_at", "author", "summary", "url"]], width="stretch", hide_index=True,
                 column_config={"url": st.column_config.LinkColumn("link", display_text="open")})


# ---------------------------------------------------------------- Customer 360
def customer_360():
    st.subheader("👥 Customer 360")
    st.caption("Every mention + Axis interaction for a linked customer, in time order.")
    cust = _safe("SELECT customer_key, customer_name FROM dim_customer ORDER BY customer_name")
    if cust.empty:
        return _empty("Seed CRM: `python sample_data/seed_crm.py`.")
    name = st.selectbox("Customer", cust["customer_name"].tolist(), key="c360")
    ck = cust[cust["customer_name"] == name].iloc[0]["customer_key"]
    timeline = _safe("""SELECT r.created_at, r.source, a.sentiment, a.score, a.intent, a.urgency, a.summary, r.url
                        FROM raw_posts r JOIN analysis a ON r.source_id=a.source_id
                        JOIN bridge_handle_customer b ON r.author=b.author
                        WHERE b.customer_key = :c ORDER BY r.created_at DESC""", {"c": ck})
    inter = _safe("""SELECT opened_at, resolved, customer_satisfied, resolution_type, response_latency_min
                     FROM fact_interaction WHERE customer_key = :c ORDER BY opened_at DESC""", {"c": ck})
    k = st.columns(3)
    k[0].metric("Mentions", len(timeline))
    k[1].metric("Interactions", len(inter))
    if len(timeline):
        k[2].metric("Avg sentiment", f"{pd.to_numeric(timeline['score'], errors='coerce').fillna(0).mean():+.2f}")
    st.markdown("**Mention timeline**")
    st.dataframe(timeline.drop(columns=["score"]), width="stretch", hide_index=True,
                 column_config={"url": st.column_config.LinkColumn("link", display_text="open")})
    if not inter.empty:
        st.markdown("**Interactions**")
        st.dataframe(inter, width="stretch", hide_index=True)


# ---------------------------------------------------------------- Languages (Hindi/Hinglish)
def language_panel():
    st.subheader("🗣️ Languages (Hindi / Hinglish)")
    st.caption("Coverage across English / Hindi / Hinglish, with sample non-English posts.")
    lang = _safe("SELECT lang, COUNT(*) n FROM clean_posts GROUP BY lang ORDER BY n DESC")
    if lang.empty:
        return _empty("Run `python -m transform.beam_transform`.")
    fig = px.pie(lang, names="lang", values="n", hole=0.55)
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=280)
    st.plotly_chart(fig, width="stretch")
    sample = _safe("""SELECT c.lang, r.author, c.clean_text, r.url
                      FROM clean_posts c JOIN raw_posts r ON c.source_id=r.source_id
                      WHERE c.lang IN ('hi','hi-en') ORDER BY r.created_at DESC LIMIT 25""")
    st.dataframe(sample, width="stretch", hide_index=True,
                 column_config={"url": st.column_config.LinkColumn("link", display_text="open")})


# ---------------------------------------------------------------- Audit log
def audit_panel():
    st.subheader("🔐 Audit Log & PII Governance")
    st.caption("Exports use masked text only and are logged here.")
    m = _safe("SELECT ts, actor, action, detail FROM audit_log ORDER BY ts DESC LIMIT 100")
    st.metric("Logged actions", len(m))
    if st.button("Export masked mentions (CSV) — logged"):
        from analytics.actions import export_masked
        df = export_masked("""SELECT r.source_id, r.source, r.author, a.text_masked, a.sentiment, a.urgency
                              FROM analysis a JOIN raw_posts r ON a.source_id=r.source_id LIMIT 500""",
                           actor="dashboard")
        st.download_button("Download masked_mentions.csv", df.to_csv(index=False),
                           "masked_mentions.csv", "text/csv")
        st.success(f"Exported {len(df)} rows (PII masked). Logged to audit_log.")
        m = _safe("SELECT ts, actor, action, detail FROM audit_log ORDER BY ts DESC LIMIT 100")
    st.dataframe(m, width="stretch", hide_index=True)
