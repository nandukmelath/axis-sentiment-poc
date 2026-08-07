"""All-in-one bank performance dashboard — the landing view.

Audience: an executive, analyst or regulator asking one question — "how is the
bank actually performing in the eyes of its customers?"  One scrolling page,
no role selector, no tabs.  Everything on it is derived from the warehouse
marts; every headline number links back to a component the reader can inspect.

Design: the Hex system in theme.py — hairline borders, 3px radii, mono eyebrow
labels, warm neutral surfaces.  Cards are hand-rolled HTML so the index hero and
the scorecards can carry structure Streamlit's own widgets don't express.

Rendered by dashboard/app.py as the default view.
"""
import os
import re

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import db
import theme
from theme import T, SENT_COLORS
from config import BRAND

POS, NEG, NEU = SENT_COLORS["positive"], SENT_COLORS["negative"], SENT_COLORS["neutral"]


# ----------------------------------------------------------------- data access
def _safe(sql, **kw):
    try:
        return db.df(sql, **kw)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=1800, show_spinner=False)
def load():
    """Every mart this page needs, in one cached round-trip.

    Cached for the same reason app.py caches: the dashboard is pointed at a
    serverless Postgres whose free tier bills egress, and the underlying data
    only moves when the harvest cron runs.
    """
    d = {}
    d["kpis"] = _safe("SELECT * FROM mart_kpis")
    d["channel"] = _safe("SELECT * FROM mart_channel ORDER BY mentions DESC")
    d["sov"] = _safe("SELECT * FROM mart_competitor_sov ORDER BY mentions DESC")
    d["products"] = _safe("SELECT * FROM mart_product_scorecard")
    d["teams"] = _safe("SELECT * FROM mart_team_queue ORDER BY open_items DESC")
    d["geo"] = _safe("SELECT * FROM mart_geo ORDER BY mentions DESC")
    d["fraud"] = _safe("SELECT * FROM mart_fraud ORDER BY cnt DESC")
    d["daily"] = _safe("""SELECT date_key, sum(mentions) mentions, sum(negatives) negatives,
                                 sum(complaints) complaints, sum(fraud_ct) fraud_ct,
                                 sum(churn_ct) churn_ct
                          FROM fact_daily GROUP BY date_key ORDER BY date_key""")
    d["mix"] = _safe("SELECT sentiment, count(*) n FROM analysis GROUP BY sentiment")
    d["intent"] = _safe("SELECT intent, count(*) n FROM analysis GROUP BY intent")
    d["urgency"] = _safe("SELECT urgency, count(*) n FROM analysis GROUP BY urgency")
    d["score"] = _safe("SELECT avg(score) s, count(*) n FROM analysis")
    d["aspects"] = _safe("""SELECT aspect, sentiment, count(*) n FROM fact_aspect_sentiment
                            GROUP BY aspect, sentiment""")
    d["drivers"] = _safe("""SELECT title, size, avg_score, top_team FROM clusters
                            WHERE avg_score < -0.05 AND size >= 3
                            ORDER BY size DESC LIMIT 12""")
    d["freshness"] = _safe("SELECT max(date_key) mx FROM fact_daily")
    # Derived, not hardcoded: the masthead count used to be a literal and went stale every
    # time a source was added. 'public' excludes the internal employee channel.
    d["srccount"] = _safe("""SELECT count(DISTINCT p.source) n FROM raw_posts p
                             LEFT JOIN dim_source s ON s.source_key = p.source
                             WHERE coalesce(s.source_type, '') <> 'employee'""")
    return d


# ----------------------------------------------------------------- the index
# Four components, each 0-100, each computed from a column a reader can query.
# Resolution performance is deliberately EXCLUDED: only 4 threads in the whole
# corpus received a bank reply, so any recovery figure would rest on n=4.  It is
# reported further down as an operational metric with its sample size attached.
WEIGHTS = {"Sentiment balance": 0.35, "Complaint load": 0.25,
           "Issue severity": 0.20, "Trust & safety": 0.20}


def compute_index(d):
    """Return (score 0-100, {component: (value, detail)}). Pure arithmetic, no model."""
    mix = dict(zip(d["mix"]["sentiment"], d["mix"]["n"])) if not d["mix"].empty else {}
    total = sum(mix.values()) or 1
    intent = dict(zip(d["intent"]["intent"], d["intent"]["n"])) if not d["intent"].empty else {}
    urg = dict(zip(d["urgency"]["urgency"], d["urgency"]["n"])) if not d["urgency"].empty else {}

    net = float(d["score"]["s"].iloc[0]) if not d["score"].empty else 0.0
    comp = {}

    # 1. Sentiment balance — mean compound score, [-1,1] rescaled to [0,100].
    comp["Sentiment balance"] = (
        (net + 1) / 2 * 100,
        f"net score {net:+.3f} across {total:,} mentions",
    )

    # 2. Complaint load — share of mentions that are complaints, inverted.
    #    A 40% complaint rate scores 0; 0% scores 100.
    cr = intent.get("complaint", 0) / total
    comp["Complaint load"] = (
        max(0.0, 1 - cr / 0.40) * 100,
        f"{intent.get('complaint', 0):,} complaints = {cr*100:.1f}% of volume",
    )

    # 3. Issue severity — share at critical/high urgency, inverted against a 25% floor.
    sev = (urg.get("critical", 0) + urg.get("high", 0)) / total
    comp["Issue severity"] = (
        max(0.0, 1 - sev / 0.25) * 100,
        f"{urg.get('critical', 0):,} critical + {urg.get('high', 0):,} high = {sev*100:.1f}%",
    )

    # 4. Trust & safety — fraud reports, churn threats and legal threats, inverted
    #    against a 10% floor (these carry more weight per unit than a complaint).
    risk = (intent.get("fraud_report", 0) + intent.get("churn_threat", 0)
            + intent.get("legal_threat", 0)) / total
    comp["Trust & safety"] = (
        max(0.0, 1 - risk / 0.10) * 100,
        f"{intent.get('fraud_report', 0)} fraud · {intent.get('churn_threat', 0)} churn · "
        f"{intent.get('legal_threat', 0)} legal = {risk*100:.1f}%",
    )

    score = sum(v[0] * WEIGHTS[k] for k, v in comp.items())
    return score, comp


def grade(s):
    for cut, g, c in [(80, "Strong", T["jade"]), (65, "Healthy", T["jade"]),
                      (50, "Watch", T["citrine"]), (35, "Strained", "#C4544F")]:
        if s >= cut:
            return g, c
    return "Critical", "#C4544F"


# ----------------------------------------------------------------- period deltas
def period_deltas(daily):
    """Last 30 days vs the 30 before it, anchored on the newest day in the data.

    Anchored on max(date_key) rather than today so the comparison stays valid
    when the harvest is behind — it answers "latest 30 days of coverage", not
    "last 30 calendar days", and the masthead states the anchor date.
    """
    if daily.empty:
        return None
    dd = daily.copy()
    dd["d"] = pd.to_datetime(dd["date_key"].astype(str), format="%Y%m%d", errors="coerce")
    dd = dd.dropna(subset=["d"])
    if dd.empty:
        return None
    end = dd["d"].max()
    cur = dd[dd["d"] > end - pd.Timedelta(days=30)]
    prv = dd[(dd["d"] <= end - pd.Timedelta(days=30)) & (dd["d"] > end - pd.Timedelta(days=60))]
    if cur.empty:
        return None

    def agg(f):
        m = f["mentions"].sum()
        return {
            "mentions": int(m),
            "neg_rate": 100 * f["negatives"].sum() / m if m else 0,
            "complaints": int(f["complaints"].sum()),
            "fraud": int(f["fraud_ct"].sum()),
        }

    return {"end": end, "cur": agg(cur), "prev": agg(prv) if not prv.empty else None}


# ----------------------------------------------------------------- normalisation
# analysis.product is free-text LLM output, so the raw scorecard splits the same
# product across several rows ('credit card' vs 'credit_card') and carries filler
# buckets ('general', 'none', 'other').  Fold them here so the table reads as a
# governed dimension rather than a tag cloud.
_PRODUCT_MAP = [
    (r"credit\s*_?card|\bcc\b", "Credit card"),
    (r"debit\s*_?card", "Debit card"),
    (r"mobile|axis\s*mobile|\bapp\b", "Mobile app"),
    (r"net\s*_?banking|internet\s*_?banking|online\s*banking", "Internet banking"),
    (r"upi|payment", "UPI & payments"),
    (r"loan|emi|mortgage", "Loans"),
    (r"fixed\s*deposit|\bfd\b|deposit", "Deposits"),
    (r"account|savings|salary", "Accounts"),
    (r"branch|atm", "Branch & ATM"),
    (r"insurance|max\s*life", "Insurance"),
    (r"forex|remit", "Forex & remittance"),
    (r"support|service|care|grievance", "Customer service"),
]
_PRODUCT_DROP = {"none", "other", "general", "banking", "banking services",
                 "unspecified", "bank", "axis bank", "n/a", ""}


def normalise_products(df):
    if df.empty:
        return df
    p = df.copy()
    p["raw"] = p["product"].fillna("").astype(str).str.strip().str.lower()
    p = p[~p["raw"].isin(_PRODUCT_DROP)]

    def canon(s):
        for pat, name in _PRODUCT_MAP:
            if re.search(pat, s):
                return name
        return None

    p["Product"] = p["raw"].map(canon)
    p = p.dropna(subset=["Product"])
    if p.empty:
        return p
    # weighted re-aggregation: rates must be re-derived from counts, not averaged
    g = p.groupby("Product").apply(
        lambda x: pd.Series({
            "Mentions": int(x["mentions"].sum()),
            "Complaints": int(x["complaints"].sum()),
            "% negative": (x["pct_negative"] * x["mentions"]).sum() / x["mentions"].sum(),
            "Avg score": (x["avg_score"] * x["mentions"]).sum() / x["mentions"].sum(),
        }), include_groups=False).reset_index()
    return g.sort_values("Mentions", ascending=False)


# ----------------------------------------------------------------- html helpers
def _eyebrow(txt):
    st.markdown(
        f'<div style="font-family:{T["mono"]};font-size:10px;letter-spacing:.14em;'
        f'text-transform:uppercase;color:var(--muted);margin:26px 0 10px 0;'
        f'padding-bottom:7px;border-bottom:1px solid var(--border)">{txt}</div>',
        unsafe_allow_html=True)


def _card(inner, pad="16px 18px"):
    return (f'<div style="background:var(--surface);border:1px solid var(--border);'
            f'border-radius:var(--r);padding:{pad};height:100%">{inner}</div>')


def _bar(label, val, detail, color):
    """A labelled 0-100 component bar for the index breakdown."""
    return f"""
    <div style="margin-bottom:13px">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:5px">
        <span style="font-size:12.5px;color:var(--text)">{label}</span>
        <span style="font-family:{T['mono']};font-size:12px;color:var(--loud);
                     font-variant-numeric:tabular-nums">{val:.0f}</span>
      </div>
      <div style="height:5px;background:var(--surface-deep);border-radius:2px;overflow:hidden">
        <div style="height:100%;width:{max(0, min(100, val)):.1f}%;background:{color}"></div>
      </div>
      <div style="font-family:{T['mono']};font-size:9.5px;color:var(--muted);
                  margin-top:4px;letter-spacing:.02em">{detail}</div>
    </div>"""


def _delta_html(cur, prev, fmt="{:,.0f}", good="down", suffix=""):
    """Signed change vs the prior period, coloured by whether the move is good."""
    if prev is None or prev == 0:
        return f'<span style="font-family:{T["mono"]};font-size:10.5px;color:var(--muted)">no prior period</span>'
    ch = cur - prev
    pct = 100 * ch / abs(prev)
    improving = (ch < 0) if good == "down" else (ch > 0)
    col = T["jade"] if improving else "#C4544F"
    if abs(pct) < 0.5:
        col = "var(--muted)"
    arrow = "▲" if ch > 0 else "▼" if ch < 0 else "▬"
    return (f'<span style="font-family:{T["mono"]};font-size:10.5px;color:{col}">'
            f'{arrow} {fmt.format(abs(ch))}{suffix} ({pct:+.0f}%) vs prior 30d</span>')


def _kpi(label, value, sub=""):
    return _card(f"""
      <div style="font-family:{T['mono']};font-size:9.5px;letter-spacing:.13em;
                  text-transform:uppercase;color:var(--muted);margin-bottom:9px">{label}</div>
      <div style="font-family:{T['display']};font-variation-settings:'wdth' 108;
                  font-size:1.65rem;font-weight:600;color:var(--loud);letter-spacing:-.03em;
                  font-variant-numeric:tabular-nums;line-height:1.1">{value}</div>
      <div style="margin-top:7px">{sub}</div>""", pad="14px 16px")


# ----------------------------------------------------------------- sections
def masthead(d):
    fresh = d["freshness"]["mx"].iloc[0] if not d["freshness"].empty else None
    asof = pd.to_datetime(str(int(fresh)), format="%Y%m%d").strftime("%d %b %Y") if fresh else "—"
    total = int(d["score"]["n"].iloc[0]) if not d["score"].empty else 0
    nsrc = int(d["srccount"]["n"].iloc[0]) if not d["srccount"].empty else 0
    st.markdown(f"""
    <div style="display:flex;justify-content:space-between;align-items:flex-end;
                border-bottom:1px solid var(--border);padding-bottom:14px;margin-bottom:6px">
      <div>
        <div style="font-family:{T['mono']};font-size:10px;letter-spacing:.16em;
                    text-transform:uppercase;color:var(--muted);margin-bottom:6px">
          Customer experience performance
        </div>
        <div style="font-family:{T['display']};font-variation-settings:'wdth' 112;
                    font-size:1.75rem;font-weight:600;color:var(--loud);letter-spacing:-.028em">
          {BRAND}
        </div>
      </div>
      <div style="text-align:right;font-family:{T['mono']};font-size:10.5px;
                  color:var(--muted);line-height:1.75">
        <div>{total:,} analysed mentions · {nsrc} public sources</div>
        <div>Coverage through <span style="color:var(--text)">{asof}</span></div>
      </div>
    </div>""", unsafe_allow_html=True)


def hero(d):
    score, comp = compute_index(d)
    g, gcol = grade(score)
    c = st.columns([0.30, 0.40, 0.30], gap="small")

    with c[0]:
        st.markdown(_card(f"""
          <div style="font-family:{T['mono']};font-size:9.5px;letter-spacing:.13em;
                      text-transform:uppercase;color:var(--muted)">Performance index</div>
          <div style="display:flex;align-items:baseline;gap:10px;margin:14px 0 2px 0">
            <span style="font-family:{T['display']};font-variation-settings:'wdth' 108;
                         font-size:3.6rem;font-weight:600;color:var(--loud);
                         letter-spacing:-.04em;line-height:.95;
                         font-variant-numeric:tabular-nums">{score:.0f}</span>
            <span style="font-family:{T['mono']};font-size:12px;color:var(--muted)">/100</span>
          </div>
          <div style="display:inline-block;margin-top:10px;padding:3px 10px;border-radius:var(--r);
                      border:1px solid {gcol};color:{gcol};font-family:{T['mono']};
                      font-size:10.5px;letter-spacing:.1em;text-transform:uppercase">{g}</div>
          <div style="font-size:11.5px;color:var(--muted);margin-top:14px;line-height:1.55">
            Weighted composite of the four components at right. Arithmetic only —
            no model, no training data. Weights and formulas are stated in the
            methodology note at the foot of this page.
          </div>"""), unsafe_allow_html=True)

    with c[1]:
        bars = "".join(
            _bar(k, v[0], v[1],
                 T["jade"] if v[0] >= 65 else T["citrine"] if v[0] >= 45 else "#C4544F")
            for k, v in comp.items())
        st.markdown(_card(
            f'<div style="font-family:{T["mono"]};font-size:9.5px;letter-spacing:.13em;'
            f'text-transform:uppercase;color:var(--muted);margin-bottom:15px">'
            f'Index components</div>{bars}'), unsafe_allow_html=True)

    with c[2]:
        mix = dict(zip(d["mix"]["sentiment"], d["mix"]["n"])) if not d["mix"].empty else {}
        tot = sum(mix.values()) or 1
        fig = go.Figure(go.Pie(
            labels=["Positive", "Neutral", "Negative"],
            values=[mix.get("positive", 0), mix.get("neutral", 0), mix.get("negative", 0)],
            hole=0.72, sort=False,
            marker=dict(colors=[POS, NEU, NEG], line=dict(color=T["surface"], width=2)),
            textinfo="none", hovertemplate="%{label}: %{value:,} (%{percent})<extra></extra>"))
        fig.update_layout(
            height=214, margin=dict(t=34, b=8, l=8, r=8), showlegend=False,
            annotations=[
                dict(text=f"<b>{100*mix.get('positive',0)/tot:.0f}%</b>", x=.5, y=.55,
                     font=dict(size=27, color=T["loud"], family=T["display"]), showarrow=False),
                dict(text="POSITIVE", x=.5, y=.38,
                     font=dict(size=9, color=T["muted"], family=T["mono"]), showarrow=False)],
            title=dict(text="SENTIMENT MIX", x=0, y=.97,
                       font=dict(size=9.5, color=T["muted"], family=T["mono"])))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def kpi_strip(d):
    p = period_deltas(d["daily"])
    k = d["kpis"].iloc[0] if not d["kpis"].empty else {}
    intent = dict(zip(d["intent"]["intent"], d["intent"]["n"])) if not d["intent"].empty else {}
    cols = st.columns(5, gap="small")

    if p:
        cur, prev = p["cur"], p["prev"]
        cols[0].markdown(_kpi("Volume · 30d", f'{cur["mentions"]:,}',
                              _delta_html(cur["mentions"], prev and prev["mentions"], good="up")),
                         unsafe_allow_html=True)
        cols[1].markdown(_kpi("Negative rate · 30d", f'{cur["neg_rate"]:.1f}%',
                              _delta_html(cur["neg_rate"], prev and prev["neg_rate"],
                                          fmt="{:.1f}", suffix="pp", good="down")),
                         unsafe_allow_html=True)
        cols[2].markdown(_kpi("Complaints · 30d", f'{cur["complaints"]:,}',
                              _delta_html(cur["complaints"], prev and prev["complaints"], good="down")),
                         unsafe_allow_html=True)
    else:
        for i, (lab, val) in enumerate([("Volume", "—"), ("Negative rate", "—"), ("Complaints", "—")]):
            cols[i].markdown(_kpi(lab, val), unsafe_allow_html=True)

    churn = intent.get("churn_threat", 0)
    cols[3].markdown(_kpi(
        "Churn threats", f"{churn:,}",
        f'<span style="font-family:{T["mono"]};font-size:10.5px;color:var(--muted)">'
        f'customers stating intent to leave</span>'), unsafe_allow_html=True)

    med = k.get("median_response_latency_min") if len(k) else None
    resp = f"{med/60:.1f}h" if med and pd.notna(med) else "—"
    cols[4].markdown(_kpi(
        "Median response", resp,
        f'<span style="font-family:{T["mono"]};font-size:10.5px;color:var(--muted)">'
        f'public reply latency · n=4</span>'), unsafe_allow_html=True)


def trend(d):
    _eyebrow("Volume and sentiment over time")
    dd = d["daily"].copy()
    if dd.empty:
        st.info("No dated facts yet — run the warehouse build.")
        return
    dd["d"] = pd.to_datetime(dd["date_key"].astype(str), format="%Y%m%d", errors="coerce")
    dd = dd.dropna(subset=["d"])
    # The corpus contains a handful of very old scraped news items (back to 2013).
    # Show the period that actually carries analysable volume.
    dd = dd[dd["d"] >= dd["d"].max() - pd.Timedelta(days=120)]
    if dd.empty:
        st.info("No recent volume.")
        return
    dd["neg_rate"] = 100 * dd["negatives"] / dd["mentions"].clip(lower=1)
    dd["roll"] = dd["neg_rate"].rolling(7, min_periods=2).mean()

    fig = go.Figure()
    fig.add_bar(x=dd["d"], y=dd["mentions"], name="Mentions",
                marker=dict(color=T["cement"], opacity=.42), yaxis="y")
    fig.add_scatter(x=dd["d"], y=dd["roll"], name="Negative rate (7d avg)",
                    mode="lines", line=dict(color=NEG, width=2), yaxis="y2")
    fig.update_layout(
        height=310, margin=dict(t=16, b=8, l=8, r=8), hovermode="x unified",
        yaxis=dict(title="mentions/day"),
        yaxis2=dict(title="% negative", overlaying="y", side="right",
                    showgrid=False, ticksuffix="%"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=""))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    st.caption("Bars are daily mention volume; the line is a 7-day rolling negative rate. "
               "Last 120 days of coverage.")


def channels(d):
    _eyebrow("Performance by channel")
    ch = d["channel"]
    if ch.empty:
        st.info("mart_channel is empty.")
        return
    c = st.columns([0.52, 0.48], gap="medium")
    with c[0]:
        x = ch.sort_values("pct_negative")
        fig = px.bar(x, x="pct_negative", y="source_type", orientation="h",
                     color="pct_negative", color_continuous_scale=[T["jade"], T["citrine"], NEG],
                     text=x["pct_negative"].map(lambda v: f"{v:.1f}%"))
        fig.update_traces(textposition="outside", textfont=dict(size=11, family=T["mono"]),
                          cliponaxis=False)
        fig.update_layout(height=250, margin=dict(t=26, b=8, l=8, r=44),
                          coloraxis_showscale=False, yaxis_title="", xaxis_title="",
                          xaxis=dict(ticksuffix="%", range=[0, max(45, x["pct_negative"].max() * 1.35)]),
                          title=dict(text="NEGATIVE RATE BY CHANNEL", x=0, y=.96,
                                     font=dict(size=9.5, color=T["muted"], family=T["mono"])))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    with c[1]:
        show = ch.rename(columns={"source_type": "Channel", "mentions": "Mentions",
                                  "pct_negative": "% neg", "avg_score": "Avg score",
                                  "complaints": "Complaints", "fraud_ct": "Fraud",
                                  "top_team": "Owning team"})
        st.dataframe(show[["Channel", "Mentions", "% neg", "Avg score", "Complaints",
                           "Fraud", "Owning team"]],
                     width="stretch", hide_index=True,
                     column_config={
                         "% neg": st.column_config.NumberColumn(format="%.1f%%"),
                         "Avg score": st.column_config.NumberColumn(format="%.3f"),
                         "Mentions": st.column_config.NumberColumn(format="%d")})
    st.caption("Social carries the most volume and the highest negative rate; news is largely "
               "neutral coverage. Channel type is assigned in the warehouse source dimension.")


def products(d):
    _eyebrow("Product and service scorecard")
    p = normalise_products(d["products"])
    if p.empty:
        st.info("No product data.")
        return
    p = p.sort_values("% negative", ascending=False)
    c = st.columns([0.55, 0.45], gap="medium")
    with c[0]:
        x = p.sort_values("% negative")
        fig = px.bar(x, x="% negative", y="Product", orientation="h",
                     color="% negative", color_continuous_scale=[T["jade"], T["citrine"], NEG],
                     custom_data=["Mentions"])
        fig.update_traces(hovertemplate="%{y}<br>%{x:.1f}% negative<br>"
                                        "%{customdata[0]:,} mentions<extra></extra>")
        fig.update_layout(height=max(230, 34 * len(x)), margin=dict(t=26, b=8, l=8, r=16),
                          coloraxis_showscale=False, yaxis_title="", xaxis_title="",
                          xaxis=dict(ticksuffix="%"),
                          title=dict(text="NEGATIVE RATE BY PRODUCT", x=0, y=.98,
                                     font=dict(size=9.5, color=T["muted"], family=T["mono"])))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    with c[1]:
        st.dataframe(p, width="stretch", hide_index=True,
                     column_config={
                         "% negative": st.column_config.ProgressColumn(
                             "% negative", format="%.1f%%", min_value=0, max_value=100),
                         "Avg score": st.column_config.NumberColumn(format="%.3f"),
                         "Mentions": st.column_config.NumberColumn(format="%d")})
    st.caption("Free-text product labels from the classifier are folded into canonical "
               "categories (`credit card` + `credit_card` → Credit card); generic buckets "
               "(`general`, `other`, `none`) are excluded. Rates are re-derived from counts, "
               "not averaged across the merged rows.")


def drivers(d):
    _eyebrow("What is driving dissatisfaction")
    c = st.columns([0.46, 0.54], gap="medium")

    with c[0]:
        a = d["aspects"]
        # 'other' is the classifier's catch-all and dwarfs every real aspect;
        # showing it would hide the signal this panel exists to surface.
        a = a[a["aspect"].notna() & (a["aspect"] != "other")] if not a.empty else a
        if a.empty:
            st.info("No aspect-level data.")
        else:
            piv = a.pivot_table(index="aspect", columns="sentiment", values="n",
                                fill_value=0, aggfunc="sum")
            for col in ["negative", "neutral", "positive"]:
                if col not in piv:
                    piv[col] = 0
            piv["tot"] = piv.sum(axis=1)
            piv = piv.sort_values("tot").tail(9)
            fig = go.Figure()
            for col, colr, nm in [("negative", NEG, "Negative"), ("neutral", NEU, "Neutral"),
                                  ("positive", POS, "Positive")]:
                fig.add_bar(y=[s.replace("_", " ") for s in piv.index], x=piv[col],
                            name=nm, orientation="h", marker_color=colr)
            fig.update_layout(barmode="stack", height=290, margin=dict(t=26, b=8, l=8, r=8),
                              legend=dict(orientation="h", y=1.06, x=0, title=""),
                              xaxis_title="", yaxis_title="",
                              title=dict(text="SENTIMENT BY SERVICE ASPECT", x=0, y=.99,
                                         font=dict(size=9.5, color=T["muted"], family=T["mono"])))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    with c[1]:
        dr = d["drivers"]
        st.markdown(f'<div style="font-family:{T["mono"]};font-size:9.5px;letter-spacing:.13em;'
                    f'text-transform:uppercase;color:var(--muted);margin-bottom:8px">'
                    f'Top negative themes</div>', unsafe_allow_html=True)
        if dr.empty:
            st.info("No negative themes above the size threshold.")
        else:
            show = dr.rename(columns={"title": "Theme", "size": "Mentions",
                                      "avg_score": "Avg score", "top_team": "Owner"})
            show["Theme"] = show["Theme"].str.slice(0, 60)
            st.dataframe(show[["Theme", "Mentions", "Avg score", "Owner"]],
                         width="stretch", hide_index=True, height=290,
                         column_config={"Avg score": st.column_config.NumberColumn(format="%.2f")})
    st.caption("Themes are clusters filtered to negative average sentiment and at least 3 "
               "mentions, so praise clusters cannot rank here. The classifier's catch-all "
               "`other` aspect is excluded from the chart.")


def operations(d):
    _eyebrow("Operational load and risk")
    c = st.columns([0.46, 0.28, 0.26], gap="medium")

    with c[0]:
        t = d["teams"]
        if t.empty:
            st.info("No team queue.")
        else:
            show = t.rename(columns={"team": "Team", "open_items": "Open", "critical": "Critical",
                                     "fraud": "Fraud", "avg_score": "Avg score"})
            show["Team"] = show["Team"].str.replace("_", " ").str.title()
            st.markdown(f'<div style="font-family:{T["mono"]};font-size:9.5px;letter-spacing:.13em;'
                        f'text-transform:uppercase;color:var(--muted);margin-bottom:8px">'
                        f'Queue by owning team</div>', unsafe_allow_html=True)
            st.dataframe(show[["Team", "Open", "Critical", "Fraud", "Avg score"]],
                         width="stretch", hide_index=True,
                         column_config={"Avg score": st.column_config.NumberColumn(format="%.2f"),
                                        "Open": st.column_config.ProgressColumn(
                                            "Open", format="%d", min_value=0,
                                            max_value=int(show["Open"].max()))})

    with c[1]:
        g = d["geo"]
        if g.empty:
            st.info("No geographic data.")
        else:
            g = g[g["mentions"] >= 3].sort_values("pct_negative", ascending=False).head(8)
            st.markdown(f'<div style="font-family:{T["mono"]};font-size:9.5px;letter-spacing:.13em;'
                        f'text-transform:uppercase;color:var(--muted);margin-bottom:8px">'
                        f'Cities by negative rate</div>', unsafe_allow_html=True)
            show = g.rename(columns={"city": "City", "mentions": "Mentions",
                                     "pct_negative": "% neg"})
            st.dataframe(show[["City", "Mentions", "% neg"]], width="stretch", hide_index=True,
                         column_config={"% neg": st.column_config.NumberColumn(format="%.0f%%")})
            st.caption("Cities named in ≥3 posts.")

    with c[2]:
        f = d["fraud"]
        intent = dict(zip(d["intent"]["intent"], d["intent"]["n"])) if not d["intent"].empty else {}
        total_fraud = int(f["cnt"].sum()) if not f.empty else 0
        st.markdown(_card(f"""
          <div style="font-family:{T['mono']};font-size:9.5px;letter-spacing:.13em;
                      text-transform:uppercase;color:var(--muted);margin-bottom:13px">
            Trust &amp; safety</div>
          <div style="display:flex;justify-content:space-between;padding:7px 0;
                      border-bottom:1px solid var(--border-soft)">
            <span style="font-size:12.5px">Fraud signals</span>
            <span style="font-family:{T['mono']};color:var(--loud)">{total_fraud}</span></div>
          <div style="display:flex;justify-content:space-between;padding:7px 0;
                      border-bottom:1px solid var(--border-soft)">
            <span style="font-size:12.5px">Fraud reports</span>
            <span style="font-family:{T['mono']};color:var(--loud)">{intent.get('fraud_report',0)}</span></div>
          <div style="display:flex;justify-content:space-between;padding:7px 0;
                      border-bottom:1px solid var(--border-soft)">
            <span style="font-size:12.5px">Legal threats</span>
            <span style="font-family:{T['mono']};color:var(--loud)">{intent.get('legal_threat',0)}</span></div>
          <div style="display:flex;justify-content:space-between;padding:7px 0">
            <span style="font-size:12.5px">Churn threats</span>
            <span style="font-family:{T['mono']};color:var(--loud)">{intent.get('churn_threat',0)}</span></div>
        """), unsafe_allow_html=True)


def benchmark(d):
    _eyebrow("Peer benchmark")
    s = d["sov"]
    if s.empty:
        st.info("No competitor data.")
        return
    c = st.columns([0.58, 0.42], gap="medium")
    with c[0]:
        x = s.sort_values("pct_negative")
        fig = go.Figure()
        fig.add_bar(y=x["brand"], x=x["pct_negative"], orientation="h",
                    marker_color=[NEG if b == BRAND else T["cement"] for b in x["brand"]],
                    text=x["pct_negative"].map(lambda v: f"{v:.1f}%"),
                    textposition="outside", textfont=dict(size=11, family=T["mono"]),
                    cliponaxis=False, hovertemplate="%{y}: %{x:.1f}% negative<extra></extra>")
        fig.update_layout(height=250, margin=dict(t=26, b=8, l=8, r=46),
                          xaxis=dict(ticksuffix="%",
                                     range=[0, max(32, x["pct_negative"].max() * 1.35)]),
                          yaxis_title="", xaxis_title="",
                          title=dict(text="NEGATIVE RATE VS PEERS", x=0, y=.96,
                                     font=dict(size=9.5, color=T["muted"], family=T["mono"])))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    with c[1]:
        show = s.rename(columns={"brand": "Brand", "mentions": "Mentions",
                                 "pct_negative": "% neg", "avg_score": "Avg score"})
        st.dataframe(show[["Brand", "Mentions", "% neg", "Avg score"]],
                     width="stretch", hide_index=True,
                     column_config={"% neg": st.column_config.NumberColumn(format="%.1f%%"),
                                    "Avg score": st.column_config.NumberColumn(format="%.3f")})
    st.warning(
        "**Not like-for-like — read before quoting.** Axis mentions are collected from all "
        "10 sources; peer mentions come from Google News RSS only and are capped per run. "
        "Peer sentiment is scored by the fast lexicon model, Axis by the LLM cascade. "
        "The gap in volume is a collection artefact, not share of voice. Treat this panel "
        "as directional until peer collection is symmetric.")


def methodology(d):
    _eyebrow("Methodology and data provenance")
    score, comp = compute_index(d)
    rows = "".join(
        f'<tr><td style="padding:5px 14px 5px 0;font-size:12px">{k}</td>'
        f'<td style="padding:5px 14px 5px 0;font-family:{T["mono"]};font-size:11.5px;'
        f'color:var(--muted)">{int(WEIGHTS[k]*100)}%</td>'
        f'<td style="padding:5px 0;font-family:{T["mono"]};font-size:11.5px;'
        f'color:var(--muted)">{v[1]}</td></tr>'
        for k, v in comp.items())
    with st.expander("How the Performance Index is computed, and what it excludes"):
        st.markdown(f"""
        <table style="width:100%;border-collapse:collapse">
          <tr style="border-bottom:1px solid var(--border)">
            <th style="text-align:left;padding:0 14px 7px 0;font-family:{T['mono']};
                       font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;
                       color:var(--muted);font-weight:500">Component</th>
            <th style="text-align:left;padding:0 14px 7px 0;font-family:{T['mono']};
                       font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;
                       color:var(--muted);font-weight:500">Weight</th>
            <th style="text-align:left;padding:0 0 7px 0;font-family:{T['mono']};
                       font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;
                       color:var(--muted);font-weight:500">Current input</th>
          </tr>{rows}
        </table>""", unsafe_allow_html=True)

        st.markdown("""
**Each component is a stated arithmetic transform, not a fitted model.**

- *Sentiment balance* — mean compound score across all analysed mentions, rescaled from [-1, 1] to [0, 100].
- *Complaint load* — share of mentions classified `complaint`, scored against a 40% ceiling.
- *Issue severity* — share at `critical` or `high` urgency, scored against a 25% ceiling.
- *Trust & safety* — combined share of `fraud_report`, `churn_threat` and `legal_threat`, scored against a 10% ceiling.

The ceilings are judgement calls, not calibrated against an external benchmark. They set how
harshly each rate is penalised; changing them moves the index but not the ranking of the components.

**Deliberately excluded from the index**

- *Resolution and recovery.* Only 4 threads in the corpus received a bank reply, so any recovery
  rate would rest on n=4. The median response latency is shown in the KPI strip with its sample
  size attached, and is not folded into the score.
- *Peer comparison.* Collection is asymmetric (see the benchmark note above), so it informs
  context but does not affect the index.

**Coverage and known limits**

- Public social, review, forum and news sources only — no internal CRM, call-centre or complaint-desk data.
- Classification is a two-stage cascade: a lexicon model scores every mention; the LLM adds
  aspect, intent, urgency and routing on the negative and ambiguous half. Roughly half the corpus
  therefore carries lexicon-level detail only, which is why aspect and team coverage is partial.
- Product labels are free text from the classifier, folded into canonical categories on this page.
- Mention volume reflects collection effort as well as customer behaviour; a rise in volume is
  not by itself a rise in customer activity.
        """)


# ----------------------------------------------------------------- entry point
def render():
    d = load()
    if d["score"].empty or not int(d["score"]["n"].iloc[0] or 0):
        st.warning("No analysed mentions yet. Run the pipeline, then reload.")
        return
    masthead(d)
    hero(d)
    kpi_strip(d)
    trend(d)
    channels(d)
    products(d)
    drivers(d)
    operations(d)
    benchmark(d)
    methodology(d)
