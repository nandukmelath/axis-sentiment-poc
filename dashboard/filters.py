"""Composable filter bar — month, category, and an arbitrary stack of conditions.

The fixed four-widget bar could express "negative fraud posts" but not "branch
posts, negative, over 500 reshares, this month", which is the question an
operator actually walks in with. So filters are data here, not widgets: a list of
{field, op, value} in session state that the user grows and prunes, applied in
order. Adding a filterable column means one entry in FIELDS.

Operators are typed to the field. A numeric column gets >= / <= / between; a
categorical one gets is / is not / is any of. Offering "greater than" on a
sentiment label is the kind of thing that makes a filter builder feel broken.
"""
import pandas as pd
import streamlit as st

from analyze.categories import CATEGORY_LABEL

# label -> (dataframe column, kind)
FIELDS = {
    "Category":        ("issue_category", "cat"),
    "Intent":          ("intent", "cat"),
    "Sentiment":       ("sentiment", "cat"),
    "Urgency":         ("urgency", "cat"),
    "Source":          ("source", "cat"),
    "Team":            ("recommended_team", "cat"),
    "Language":        ("lang", "cat"),
    "Likes":           ("engagement", "num"),
    "Reshares":        ("retweet_count", "num"),
    "Comments":        ("reply_count", "num"),
    "Views":           ("view_count", "num"),
    "Sentiment score": ("score", "num"),
    "Fraud flag":      ("fraud_signal", "bool"),
    "Churn risk":      ("churn_risk", "bool"),
    "PII present":     ("pii_present", "bool"),
}
OPS = {"cat": ["is", "is not", "is any of"], "num": ["≥", "≤", "between"], "bool": ["is"]}

# Columns worth sorting by, with the direction toggle handled separately.
SORT_FIELDS = {
    "Triage priority": None,          # computed, not a column
    "Newest": "created_dt",
    "Likes": "engagement",
    "Reshares": "retweet_count",
    "Comments": "reply_count",
    "Views": "view_count",
    "Reach": "reach",
    "Sentiment score": "score",
}

_STATE = "nr_filters"


def _label_for(col, value):
    return CATEGORY_LABEL.get(value, value) if col == "issue_category" else value


def _options(d, col):
    vals = [v for v in d[col].dropna().unique().tolist() if str(v) != ""]
    return sorted(vals, key=lambda v: str(_label_for(col, v)).lower())


def month_options(d):
    """Distinct months present, newest first, as (label, Period)."""
    if d.empty or "created_dt" not in d:
        return []
    per = d["created_dt"].dropna().dt.to_period("M")
    return [(p.strftime("%b %Y"), p) for p in sorted(per.unique(), reverse=True)]


def apply_month(d, period):
    if period is None or d.empty:
        return d
    return d[d["created_dt"].dt.to_period("M") == period]


def apply_one(d, f):
    """Apply a single {field, op, value}. Unknown or half-built filters pass
    through untouched so the table never blanks out mid-edit."""
    spec = FIELDS.get(f.get("field"))
    if not spec or d.empty:
        return d
    col, kind = spec
    if col not in d.columns:
        return d
    op, val = f.get("op"), f.get("value")

    if kind == "cat":
        if val in (None, "", []):
            return d
        if op == "is":
            return d[d[col].astype(str) == str(val)]
        if op == "is not":
            return d[d[col].astype(str) != str(val)]
        return d[d[col].astype(str).isin([str(v) for v in val])]

    if kind == "num":
        s = pd.to_numeric(d[col], errors="coerce")
        if op == "≥":
            return d[s >= float(val or 0)]
        if op == "≤":
            return d[s <= float(val or 0)]
        lo, hi = (val or (0, 0))
        return d[(s >= float(lo)) & (s <= float(hi))]

    truthy = pd.to_numeric(d[col], errors="coerce").fillna(0) > 0
    return d[truthy if val in (True, "Yes", 1) else ~truthy]


def apply_all(d, filters):
    for f in filters or []:
        d = apply_one(d, f)
    return d


def describe(f):
    """Human-readable filter, used for the active-filter chips."""
    spec = FIELDS.get(f.get("field"))
    if not spec:
        return ""
    col, kind = spec
    val = f.get("value")
    if kind == "cat":
        shown = (", ".join(str(_label_for(col, v)) for v in val)
                 if isinstance(val, list) else _label_for(col, val))
    elif kind == "num" and f.get("op") == "between":
        lo, hi = val or (0, 0)
        shown = f"{lo:g}–{hi:g}"
    else:
        shown = val
    return f"{f['field']} {f.get('op', '')} {shown}".strip()


# ------------------------------------------------------------------ UI
def _ensure():
    if _STATE not in st.session_state:
        st.session_state[_STATE] = []


def filter_builder(d):
    """Render the stack and return the current filter list."""
    _ensure()
    filters = st.session_state[_STATE]

    for i, f in enumerate(list(filters)):
        c = st.columns([0.26, 0.2, 0.44, 0.1])
        field = c[0].selectbox("Field", list(FIELDS), key=f"f_field_{i}",
                               index=list(FIELDS).index(f["field"]) if f.get("field") in FIELDS else 0,
                               label_visibility="collapsed")
        col, kind = FIELDS[field]
        # Changing the field invalidates the operator and value — a "between"
        # left over from a numeric field would crash against a text column.
        if field != f.get("field"):
            f.update({"field": field, "op": OPS[kind][0], "value": None})

        op = c[1].selectbox("Op", OPS[kind], key=f"f_op_{i}",
                            index=OPS[kind].index(f["op"]) if f.get("op") in OPS[kind] else 0,
                            label_visibility="collapsed")
        f["op"] = op

        if kind == "cat":
            opts = _options(d, col)
            fmt = (lambda v: str(_label_for(col, v)))
            if op == "is any of":
                cur = [v for v in (f.get("value") or []) if v in opts]
                f["value"] = c[2].multiselect("Value", opts, default=cur, format_func=fmt,
                                              key=f"f_val_{i}", label_visibility="collapsed")
            else:
                idx = opts.index(f["value"]) if f.get("value") in opts else 0
                f["value"] = c[2].selectbox("Value", opts, index=idx, format_func=fmt,
                                            key=f"f_val_{i}", label_visibility="collapsed") if opts else None
        elif kind == "num":
            series = pd.to_numeric(d[col], errors="coerce") if col in d else pd.Series([0])
            hi_default = float(series.max() or 0)
            if op == "between":
                cur = f.get("value") or (0.0, hi_default)
                f["value"] = c[2].slider("Value", 0.0, max(hi_default, 1.0),
                                         value=(float(cur[0]), float(cur[1])),
                                         key=f"f_val_{i}", label_visibility="collapsed")
            else:
                f["value"] = c[2].number_input("Value", value=float(f.get("value") or 0),
                                               step=1.0, key=f"f_val_{i}",
                                               label_visibility="collapsed")
        else:
            f["value"] = c[2].radio("Value", ["Yes", "No"], horizontal=True,
                                    index=0 if f.get("value") in (None, "Yes") else 1,
                                    key=f"f_val_{i}", label_visibility="collapsed")

        if c[3].button("✕", key=f"f_del_{i}", help="Remove this filter"):
            filters.pop(i)
            st.rerun()

    if st.button("＋ Add filter", key="f_add"):
        filters.append({"field": "Category", "op": "is", "value": None})
        st.rerun()
    if filters and st.button("Clear all", key="f_clear"):
        st.session_state[_STATE] = []
        st.rerun()

    return filters
