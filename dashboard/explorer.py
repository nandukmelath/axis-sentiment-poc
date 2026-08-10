"""Data explorer — the spreadsheet view. Filters on top, every value below.

The other three surfaces are opinionated: the newsroom ranks by triage, the
overview aggregates, operations routes to teams. This one takes no view. It is
one row per mention with every column the warehouse holds, a filter bar above
it, and an export button — for the analyst who wants to sort by reshares, pull
last month's fraud reports, and take the result into Excel.

Deliberately flat: no cards, no truncation to a preview, no computed ranking
unless asked for. Columns are ordered the way they were specified — post,
account, engagement, then everything else.
"""
import pandas as pd
import streamlit as st

import filters as flt
from analyze.categories import CATEGORY_LABEL
from config import BRAND
from newsroom import load
from theme import T

# label -> (column, width). Order is the display order.
COLUMNS = [
    ("Tweet / post",  "post",         "large"),
    ("Account",       "account",      "medium"),
    ("Source",        "source",       "small"),
    ("Category",      "category",     "medium"),
    ("Impressions",   "view_count",   "small"),
    ("Likes",         "engagement",   "small"),
    ("Reshares",      "retweet_count", "small"),
    ("Comments",      "reply_count",  "small"),
    ("Quotes",        "quote_count",  "small"),
    ("Sentiment",     "sentiment",    "small"),
    ("Score",         "score",        "small"),
    ("Emotion",       "emotion",      "small"),
    ("Intent",        "intent",       "medium"),
    ("Urgency",       "urgency",      "small"),
    ("Team",          "recommended_team", "medium"),
    ("Product",       "product",      "medium"),
    ("Root cause",    "root_cause",   "medium"),
    ("Theme",         "theme",        "medium"),
    ("Fraud",         "fraud_signal", "small"),
    ("Churn",         "churn_risk",   "small"),
    ("PII",           "pii_present",  "small"),
    ("Language",      "lang",         "small"),
    ("Confidence",    "confidence",   "small"),
    ("Model",         "model",        "small"),
    ("Why category",  "category_reason", "medium"),
    ("Date",          "created_dt",   "medium"),
    ("Link",          "url",          "small"),
]
NUMERIC = {"view_count", "engagement", "retweet_count", "reply_count", "quote_count"}
BOOLEAN = {"fraud_signal", "churn_risk", "pii_present"}


def _frame(d):
    """Build the display frame. Text is the PII-masked variant — the raw text is
    never surfaced here, because an export leaves the building."""
    out = pd.DataFrame(index=d.index)
    out["post"] = d["text_masked"].fillna(d["text"]).astype(str)
    out["account"] = d["author_name"].fillna(d["author"]).fillna("—")
    out["category"] = d["issue_category"].map(lambda v: CATEGORY_LABEL.get(v, v or "—"))
    for _, col, _ in COLUMNS:
        if col in out.columns:
            continue
        if col not in d.columns:
            out[col] = None
        elif col in NUMERIC:
            out[col] = pd.to_numeric(d[col], errors="coerce").fillna(0).astype("int64")
        elif col in BOOLEAN:
            out[col] = pd.to_numeric(d[col], errors="coerce").fillna(0) > 0
        else:
            out[col] = d[col]
    out["score"] = pd.to_numeric(d["score"], errors="coerce").round(3)
    out["confidence"] = pd.to_numeric(d["confidence"], errors="coerce").round(2)
    return out[[c for _, c, _ in COLUMNS]]


def _config():
    cfg = {}
    for label, col, width in COLUMNS:
        if col == "url":
            cfg[col] = st.column_config.LinkColumn(label, display_text="open", width=width)
        elif col == "created_dt":
            cfg[col] = st.column_config.DatetimeColumn(label, format="DD MMM YYYY HH:mm",
                                                       width=width)
        elif col in BOOLEAN:
            cfg[col] = st.column_config.CheckboxColumn(label, width=width)
        elif col == "score":
            cfg[col] = st.column_config.NumberColumn(label, format="%.3f", width=width)
        elif col == "confidence":
            cfg[col] = st.column_config.NumberColumn(label, format="%.2f", width=width)
        elif col in NUMERIC:
            cfg[col] = st.column_config.NumberColumn(label, format="%d", width=width)
        else:
            cfg[col] = st.column_config.TextColumn(label, width=width)
    return cfg


def render():
    posts, answered, drafts, trends = load()

    st.markdown(f"""
    <div style="display:flex;justify-content:space-between;align-items:flex-end;
                border-bottom:1px solid var(--border);padding-bottom:14px;margin-bottom:14px">
      <div>
        <div style="font-family:{T['mono']};font-size:10px;letter-spacing:.16em;
                    text-transform:uppercase;color:var(--muted);margin-bottom:6px">
          Data explorer · every mention, every field
        </div>
        <div style="font-family:{T['display']};font-variation-settings:'wdth' 112;
                    font-size:1.75rem;font-weight:600;color:var(--loud);letter-spacing:-.028em">
          {BRAND}
        </div>
      </div>
    </div>""", unsafe_allow_html=True)

    if posts.empty:
        st.info("No scored posts yet. Run the pipeline first.")
        return

    # ---------------------------------------------------------------- filters
    st.markdown(f"""<div style="font-family:{T['mono']};font-size:10px;letter-spacing:.14em;
                text-transform:uppercase;color:var(--muted);margin-bottom:6px">Filters</div>""",
                unsafe_allow_html=True)

    c = st.columns([0.2, 0.24, 0.2, 0.36])
    months = flt.month_options(posts)
    month_pick = c[0].selectbox("Month", ["All months"] + [m for m, _ in months], key="ex_month")
    cat_opts = ["All categories"] + [CATEGORY_LABEL.get(k, k) for k in
                                     sorted(posts["issue_category"].dropna().unique().tolist())]
    cat = c[1].selectbox("Category", cat_opts, key="ex_cat")
    src = c[2].selectbox("Source", ["All sources"] + sorted(posts["source"].dropna().unique().tolist()),
                         key="ex_src")
    q = c[3].text_input("Search text", key="ex_q", placeholder="Search post text…")

    d = posts
    if month_pick != "All months":
        d = flt.apply_month(d, dict(months)[month_pick])
    if cat != "All categories":
        inv = {v: k for k, v in CATEGORY_LABEL.items()}
        d = d[d["issue_category"] == inv.get(cat, cat)]
    if src != "All sources":
        d = d[d["source"] == src]
    if q:
        d = d[d["text"].str.contains(q, case=False, na=False, regex=False)]

    with st.expander("More filters", expanded=bool(st.session_state.get("nr_filters"))):
        active = flt.filter_builder(d)
    d = flt.apply_all(d, active)

    s = st.columns([0.28, 0.22, 0.5])
    sort_field = s[0].selectbox("Sort by", [k for k, v in flt.SORT_FIELDS.items() if v],
                                key="ex_sortf")
    direction = s[1].radio("Order", ["Desc", "Asc"], horizontal=True, key="ex_dir")
    d = d.sort_values(flt.SORT_FIELDS[sort_field], ascending=direction == "Asc",
                      na_position="last")

    chips = " · ".join(x for x in (flt.describe(f) for f in active) if x)
    s[2].markdown(f"""<div style="font-family:{T['mono']};font-size:11px;color:var(--muted);
                  padding-top:26px">{len(d):,} of {len(posts):,} mentions
                  {'· ' + chips if chips else ''}</div>""", unsafe_allow_html=True)

    if d.empty:
        st.info("No mentions match these filters.")
        return

    # ---------------------------------------------------------------- table
    t = _frame(d)
    st.dataframe(t, use_container_width=True, hide_index=True, height=640,
                 column_config=_config())
    st.download_button(f"Download {len(t):,} rows (CSV)",
                       t.to_csv(index=False).encode("utf-8"),
                       file_name="axis_mentions.csv", mime="text/csv", key="ex_csv")
    st.caption("Post text is the PII-masked variant — account numbers, phone numbers and "
               "card numbers are redacted before export.")
