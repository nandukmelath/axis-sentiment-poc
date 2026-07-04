"""Axis Bank Social Sentiment — LIVE war-room UI.
Reads axis.db. Auto-updates: KPIs/clock every 1s, charts every 6s (st.fragment),
plus a continuously scrolling mention ticker. Feels live; shows real new data the
moment run_fetch/run_all lands it (run live_ingest.py alongside for a true stream).

Run:  streamlit run dashboard/app.py
"""
import sys, os, json, html, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import plotly.express as px
import streamlit as st

import db
import panels
from config import BRAND

st.set_page_config(page_title=f"{BRAND} — Live Sentiment War-Room", layout="wide",
                   initial_sidebar_state="expanded")

SENT_COLORS = {"negative": "#C0392B", "positive": "#2E8B57", "neutral": "#7F8C8D", "mixed": "#E67E22"}
URG_W = {"critical": 4, "high": 3, "medium": 2, "low": 1}

st.markdown("""
<style>
.live-badge{display:inline-flex;align-items:center;gap:7px;font-weight:700;color:#C0392B;font-size:15px}
.live-dot{height:11px;width:11px;background:#e11;border-radius:50%;display:inline-block;
  box-shadow:0 0 0 0 rgba(225,17,17,.7);animation:pulse 1.2s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(225,17,17,.7)}70%{box-shadow:0 0 0 10px rgba(225,17,17,0)}100%{box-shadow:0 0 0 0 rgba(225,17,17,0)}}
.ticker{overflow:hidden;white-space:nowrap;background:#0b1020;color:#e8eef7;border-radius:8px;
  padding:8px 0;margin:4px 0 6px 0;border:1px solid #22304a}
.ticker-track{display:inline-block;padding-left:100%;animation:scroll 45s linear infinite}
.ticker:hover .ticker-track{animation-play-state:paused}
@keyframes scroll{0%{transform:translateX(0)}100%{transform:translateX(-100%)}}
.tk{margin:0 26px;font-size:13px}
.clock{font-variant-numeric:tabular-nums;color:#5C7088;font-weight:600}
</style>
""", unsafe_allow_html=True)


def fresh():
    a = db.df("""SELECT a.*, r.text, r.url, r.source, r.author, r.created_at, r.engagement,
                        COALESCE(cp.lang, r.lang, 'en') AS lang
                 FROM analysis a JOIN raw_posts r ON a.source_id = r.source_id
                 LEFT JOIN clean_posts cp ON a.source_id = cp.source_id""")
    c = db.df("SELECT * FROM clusters ORDER BY size DESC")
    if not a.empty:
        a["created_dt"] = pd.to_datetime(a["created_at"], errors="coerce", utc=True)
        a["date"] = a["created_dt"].dt.date
    return a, c


def apply_filters(a):
    ss = st.session_state
    fa = a.copy()
    if ss.get("f_src"):  fa = fa[fa["source"].isin(ss["f_src"])]
    if ss.get("f_sent"): fa = fa[fa["sentiment"].isin(ss["f_sent"])]
    if ss.get("f_urg"):  fa = fa[fa["urgency"].isin(ss["f_urg"])]
    if ss.get("f_team"): fa = fa[fa["recommended_team"].isin(ss["f_team"])]
    if ss.get("f_lang") and "lang" in fa: fa = fa[fa["lang"].isin(ss["f_lang"])]
    if ss.get("f_q"):    fa = fa[fa["text"].str.contains(ss["f_q"], case=False, na=False)]
    if ss.get("f_flags"): fa = fa[(fa["fraud_signal"] == 1) | (fa["churn_risk"] == 1)]
    delta = {"1 hour": datetime.timedelta(hours=1), "1 day": datetime.timedelta(days=1),
             "1 month": datetime.timedelta(days=30)}.get(ss.get("t_window"))
    if delta is not None and "created_dt" in fa.columns:
        fa = fa[fa["created_dt"] >= pd.Timestamp.now(tz="UTC") - delta]
    return fa


def explode_aspects(a):
    rows = []
    for js in a["aspects_json"].dropna():
        try:
            for it in json.loads(js):
                if isinstance(it, dict):
                    rows.append({"aspect": it.get("aspect"), "sentiment": it.get("sentiment")})
        except Exception:
            pass
    return pd.DataFrame(rows)


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINDOW_CODE = {"1 hour": "1h", "1 day": "1d", "1 month": "1m"}


def _run_pipeline(window_label):
    """Fetch Axis mentions for the window + refresh everything (runs run_window.py)."""
    import subprocess
    code = WINDOW_CODE.get(window_label)
    args = [sys.executable, "-m", "run_window"] + (["--window", code] if code else [])
    with st.spinner(f"Fetching Axis mentions ({window_label}) + refreshing the board…"):
        try:
            r = subprocess.run(args, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=900)
            ok, out = r.returncode == 0, (r.stdout or "") + (r.stderr or "")
        except Exception as e:
            ok, out = False, str(e)
    st.cache_data.clear()
    if ok:
        line = next((l for l in out.splitlines() if l.startswith("RUN_WINDOW")), "done")
        st.success(f"Run complete — board refreshed. {line[:200]}")
    else:
        st.error(f"Run failed: {out[-400:]}")
    st.rerun()


# ---------------- header ----------------
st.title(f"🏦 {BRAND} — Live Sentiment War-Room")
st.caption("Real-time customer voice across social media · AI sentiment by Gemini · in-house / DPDP-friendly")

# ---------------- RUN bar (main page — always visible) ----------------
_rb = st.columns([0.22, 0.24, 0.54])
_rb[0].selectbox("⏱ Time window", ["All time", "1 hour", "1 day", "1 month"], key="t_window",
                 label_visibility="collapsed")
if _rb[1].button("▶ Run fetch + refresh", type="primary"):
    _run_pipeline(st.session_state.get("t_window", "All time"))
_rb[2].caption("Pick a window → fetches Axis mentions for it → DB → the board reflects that window.")

# ---------------- sidebar: role selector + filters ----------------
_a0, _ = fresh()
ROLE_TABS = {
    "Exec": ["🛰️ War-Room", "🏁 Competitor SOV", "📦 Products", "📈 Trends", "🗞️ Digest"],
    "RM": ["👤 RM Cockpit", "👥 Customer 360", "✍️ Drafts"],
    "Ops": ["🛠️ Admin", "🧭 Team Queues", "🛡️ Fraud", "🔔 Alerts", "✍️ Drafts"],
    "Analyst": ["📈 Trends", "🗺️ Geo", "📢 Influencers", "🔬 Root-cause", "🗣️ Languages", "📦 Products"],
    "Admin (all)": None,   # all tabs
}
st.sidebar.header("View")
role = st.sidebar.selectbox("Role", list(ROLE_TABS), key="role")
st.sidebar.header("Filters")
st.sidebar.multiselect("Source", sorted(_a0["source"].dropna().unique()) if not _a0.empty else [], key="f_src")
st.sidebar.multiselect("Sentiment", ["negative", "mixed", "neutral", "positive"], key="f_sent")
st.sidebar.multiselect("Urgency", ["critical", "high", "medium", "low"], key="f_urg")
st.sidebar.multiselect("Team", sorted(_a0["recommended_team"].dropna().unique()) if not _a0.empty else [], key="f_team")
st.sidebar.multiselect("Language", ["en", "hi", "hi-en"], key="f_lang")
st.sidebar.text_input("Search text", key="f_q")
st.sidebar.checkbox("Only fraud / churn flagged", key="f_flags")
st.sidebar.divider()
st.sidebar.caption("KPIs refresh every 1s · charts every 6s · ticker streams live.")

if "baseline" not in st.session_state:
    st.session_state.baseline = len(_a0)

if _a0.empty:
    st.warning("No analyzed posts yet. Run `python run_all.py`, then this updates automatically.")
    st.stop()


# ---------------- LIVE strip (every 1s) ----------------
@st.fragment(run_every="1s")
def live_strip():
    a, clusters = fresh()
    fa = apply_filters(a)
    now = datetime.datetime.now().strftime("%H:%M:%S")
    top = st.columns([0.5, 0.5])
    top[0].markdown(f'<span class="live-badge"><span class="live-dot"></span>LIVE</span>'
                    f'&nbsp;&nbsp;<span class="clock">{now}</span>', unsafe_allow_html=True)
    delta = len(a) - st.session_state.baseline
    top[1].markdown(f'<div style="text-align:right" class="clock">total mentions: '
                    f'<b>{len(a)}</b> &nbsp;·&nbsp; +{delta} since open</div>', unsafe_allow_html=True)

    # emerging banner
    em = clusters[(clusters["recent_share"] >= 0.6) & (clusters["avg_score"] < 0) & (clusters["size"] >= 2)] \
        if not clusters.empty else pd.DataFrame()
    if not em.empty:
        t = em.sort_values("size", ascending=False).iloc[0]
        st.error(f"🚨 **EMERGING** — \"{t['title']}\" · {int(t['size'])} mentions · "
                 f"{int(t['recent_share']*100)}% in last 24h · owner: **{t['top_team']}** · act now.")

    k = st.columns(6)
    k[0].metric("Mentions", len(fa))
    net = fa["score"].mean() if len(fa) else 0
    k[1].metric("Net sentiment", f"{net:+.2f}")
    negp = 100 * fa["sentiment"].isin(["negative", "mixed"]).mean() if len(fa) else 0
    k[2].metric("% negative", f"{negp:.0f}%")
    k[3].metric("Complaints", int((fa["intent"] == "complaint").sum()))
    k[4].metric("🔴 Critical", int((fa["urgency"] == "critical").sum()))
    k[5].metric("🛡️ Fraud", int(fa["fraud_signal"].sum()))

    # scrolling ticker of newest mentions
    recent = fa.sort_values("created_dt", ascending=False, na_position="last").head(30)
    items = []
    for _, r in recent.iterrows():
        col = SENT_COLORS.get(r["sentiment"], "#ccc")
        txt = html.escape((r["text"] or "")[:110])
        items.append(f'<span class="tk"><b style="color:{col}">●</b> '
                     f'<span style="color:#9fb3c8">[{r["source"]}]</span> {txt}</span>')
    st.markdown(f'<div class="ticker"><div class="ticker-track">{"".join(items)}</div></div>',
                unsafe_allow_html=True)


# ---------------- analytics (every 6s) ----------------
@st.fragment(run_every="6s")
def analytics():
    a, clusters = fresh()
    fa = apply_filters(a)
    st.divider()
    r1 = st.columns([0.34, 0.4, 0.26])
    with r1[0]:
        st.subheader("Sentiment mix")
        sc = fa["sentiment"].value_counts().reset_index(); sc.columns = ["sentiment", "n"]
        fig = px.pie(sc, names="sentiment", values="n", hole=0.55, color="sentiment", color_discrete_map=SENT_COLORS)
        fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300)
        st.plotly_chart(fig, width="stretch")
    with r1[1]:
        st.subheader("Volume & sentiment trend")
        tr = fa.dropna(subset=["date"]).groupby(["date", "sentiment"]).size().reset_index(name="n")
        if tr.empty:
            st.info("No timestamps.")
        else:
            fig = px.area(tr, x="date", y="n", color="sentiment", color_discrete_map=SENT_COLORS)
            fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300, legend_title="")
            st.plotly_chart(fig, width="stretch")
    with r1[2]:
        st.subheader("Emotion")
        ec = fa["emotion"].value_counts().reset_index(); ec.columns = ["emotion", "n"]
        fig = px.bar(ec, x="n", y="emotion", orientation="h")
        fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300, yaxis_title="", xaxis_title="")
        st.plotly_chart(fig, width="stretch")

    r2 = st.columns(2)
    with r2[0]:
        st.subheader("Where it hurts — aspect sentiment")
        asp = explode_aspects(fa)
        if asp.empty:
            st.info("No aspect data.")
        else:
            ag = asp.groupby(["aspect", "sentiment"]).size().reset_index(name="n")
            fig = px.bar(ag, x="n", y="aspect", color="sentiment", orientation="h", color_discrete_map=SENT_COLORS)
            fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=340, yaxis_title="", xaxis_title="mentions", legend_title="")
            st.plotly_chart(fig, width="stretch")
    with r2[1]:
        st.subheader("Mentions by source")
        srcc = fa["source"].value_counts().reset_index(); srcc.columns = ["source", "n"]
        fig = px.bar(srcc, x="source", y="n")
        fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=340, xaxis_title="", yaxis_title="")
        st.plotly_chart(fig, width="stretch")

    st.subheader("🔎 Top issues (auto-clustered)")
    if not clusters.empty:
        cc = clusters.copy()
        cc["🚨"] = ((cc["recent_share"] >= 0.6) & (cc["avg_score"] < 0) & (cc["size"] >= 2)).map({True: "🚨", False: ""})
        cc["recent%"] = (cc["recent_share"] * 100).round().astype(int)
        cc = cc.rename(columns={"title": "issue", "size": "mentions", "avg_score": "avg sentiment", "top_team": "owner"})
        st.dataframe(cc[["🚨", "issue", "mentions", "recent%", "avg sentiment", "owner"]], width="stretch", hide_index=True)

    st.subheader("⚡ Priority action queue")
    pq = fa.copy()
    pq["priority"] = pq["urgency"].map(URG_W).fillna(1) * (1 + pq["engagement"].fillna(0) ** 0.5) * (0.5 + (-pq["score"]).clip(lower=0))
    pq = pq.sort_values("priority", ascending=False).head(15)
    st.dataframe(pq[["urgency", "sentiment", "recommended_team", "recommended_action", "summary", "url"]],
                 width="stretch", hide_index=True,
                 column_config={"url": st.column_config.LinkColumn("link", display_text="open"),
                                "recommended_team": st.column_config.TextColumn("team"),
                                "recommended_action": st.column_config.TextColumn("action")})

    with st.expander("📋 Executive brief"):
        if os.path.exists("exec_summary.md"):
            st.markdown(open("exec_summary.md", encoding="utf-8").read())
        else:
            st.info("Run `python -m analyze.exec_summary`.")


def _safe(sql):
    try:
        return db.df(sql)
    except Exception:
        return pd.DataFrame()


# ---------------- RM COCKPIT (warehouse mart) ----------------
def rm_cockpit():
    st.subheader("👤 RM Cockpit — know the customer before you call")
    st.caption("Per-customer pain point + next-best cross-sell, from social voice joined to CRM.")
    m = _safe("SELECT * FROM mart_rm_enablement")
    if m.empty:
        st.info("No RM data yet. Run `python sample_data/seed_crm.py` then `python -m warehouse.build`.")
        return
    names = m["customer_name"].tolist()
    pick = st.selectbox("Customer", names, key="rm_pick")
    r = m[m["customer_name"] == pick].iloc[0]
    c = st.columns(4)
    c[0].metric("Segment", r["segment"])
    c[1].metric("RM", r["rm_name"] or r["rm_id"])
    c[2].metric("Sentiment", r["current_sentiment"], delta=r["sentiment_trend"])
    c[3].metric("Open issues", int(r["open_issues"]))
    flags = []
    if int(r["churn_flag"] or 0):
        flags.append("⚠️ churn risk")
    if int(r["fraud_flag"] or 0):
        flags.append("🛡️ fraud signal")
    if flags:
        st.warning(" · ".join(flags))
    st.markdown(f"**Pain point ({r['top_pain_area']}):** {r['top_pain_point']}")
    st.markdown(f"**Products held:** {r['products_held']}")
    st.success(f"**Cross-sell → {r['cross_sell_product']}**  \n{r['cross_sell_pitch']}")
    st.markdown(f"**Last public interaction:** {r['last_interaction_outcome']}")
    st.info(f"🗣️ **Talking point:** {r['talking_point']}")
    st.divider()
    st.caption("All linked customers")
    st.dataframe(m[["customer_name", "segment", "rm_name", "current_sentiment", "sentiment_trend",
                    "top_pain_area", "cross_sell_product", "last_interaction_outcome"]],
                 width="stretch", hide_index=True)


# ---------------- ADMIN ANALYTICS (warehouse marts) ----------------
def admin_analytics():
    st.subheader("🛠️ Admin Analytics — resolution loop & follow-up")
    k = _safe("SELECT * FROM mart_kpis")
    if k.empty:
        st.info("No admin data yet. Run `python -m warehouse.build`.")
        return
    row = k.iloc[0]
    c = st.columns(5)
    c[0].metric("Total mentions", int(row["total_mentions"]))
    c[1].metric("% negative", f"{row['pct_negative']:.0f}%")
    c[2].metric("Needs follow-up", int(row["needs_followup"]))
    c[3].metric("⭐ Sentiment Recovery", f"{row['sentiment_recovery_rate']:.0f}%",
                help="Of complaints Axis responded to, % where the customer's sentiment recovered to neutral/positive. The north-star.")
    med = row["median_response_latency_min"]
    c[4].metric("Median response", f"{med/60:.1f}h" if pd.notna(med) else "—")

    adf = _safe("SELECT * FROM mart_admin_analytics")
    if not adf.empty:
        st.markdown("**Follow-up bifurcation** — by RBI category × owning team")
        show = adf.rename(columns={"category": "RBI category", "team": "team", "mentions": "mentions",
                                   "pct_negative": "% neg", "no_followup": "no f/u", "pending": "pending",
                                   "in_progress": "in prog", "resolved": "resolved", "unresolved": "unresolved"})
        st.dataframe(show[["RBI category", "team", "mentions", "% neg", "no f/u", "pending",
                           "in prog", "resolved", "unresolved"]], width="stretch", hide_index=True)
        melt = adf.melt(id_vars=["category"], value_vars=["pending", "in_progress", "resolved", "unresolved"],
                        var_name="status", value_name="n")
        melt = melt[melt["n"] > 0]
        if not melt.empty:
            fig = px.bar(melt, x="category", y="n", color="status", barmode="stack")
            fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=320, xaxis_title="", legend_title="")
            st.plotly_chart(fig, width="stretch")

    idf = _safe("SELECT * FROM fact_interaction ORDER BY opened_at DESC")
    if not idf.empty:
        st.markdown("**Interaction log** — public complaints Axis replied to")
        idf = idf.rename(columns={"customer_key": "customer", "response_latency_min": "resp (min)",
                                  "customer_satisfied": "satisfied", "resolution_type": "type",
                                  "recovery_delta": "recovery"})
        st.dataframe(idf[["author", "customer", "resolved", "satisfied", "type",
                          "resp (min)", "recovery"]], width="stretch", hide_index=True)


# ---------------- role-driven tabs ----------------
def war_room():
    live_strip()
    analytics()


TAB_FUNCS = {
    "🛰️ War-Room": war_room,
    "👤 RM Cockpit": rm_cockpit,
    "🛠️ Admin": admin_analytics,
    "🏁 Competitor SOV": panels.competitor_sov,
    "📦 Products": panels.product_scorecards,
    "📈 Trends": panels.trends_panel,
    "🗺️ Geo": panels.geo_panel,
    "📢 Influencers": panels.influencers_panel,
    "🧭 Team Queues": panels.team_queues,
    "🛡️ Fraud": panels.fraud_board,
    "🔔 Alerts": panels.alerts_panel,
    "✍️ Drafts": panels.response_drafts,
    "🗞️ Digest": panels.weekly_digest_panel,
    "🔬 Root-cause": panels.root_cause_panel,
    "👥 Customer 360": panels.customer_360,
    "🗣️ Languages": panels.language_panel,
    "🔐 Audit": panels.audit_panel,
}

_names = ROLE_TABS.get(role) or list(TAB_FUNCS.keys())
for _t, _nm in zip(st.tabs(_names), _names):
    with _t:
        TAB_FUNCS[_nm]()
