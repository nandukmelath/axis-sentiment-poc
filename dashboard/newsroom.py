"""Signal Feed — the newsroom / post-explorer view.

Where the Performance dashboard answers "how are we doing?" in aggregate, this view
answers "what are people actually saying, and which posts do I act on?". You read the
real posts, browse by lane, and every card carries the full AI read plus its provenance.

Built to the project lead's sketch (home / post-by-issue-category / highlight rail),
with five data-vetted insight lanes folded in (see NEWSROOM-INSIGHTS.md):

  1. Response-Gap War Room  high-reach negatives the bank never answered
  2. Red-Flag Desk          fraud + churn + legal + urgency fused into one liability lane
  3. Triage sort            urgency x intent x reach x severity ranking
  4. Fraud Fast-Lane        fraud_signal posts with a PII-redaction guard
  5. Model Trust            per-card provenance; lexicon rows are visibly dimmed

Honesty constraints baked in, because the data has real gaps:
  - engagement/views exist on Twitter only (~49% of corpus), so reach-ranked rails say "tweets"
  - 963 rows are lexicon-scored and ALL carry emotion='joy' (a degenerate default) -> we
    suppress emotion on those rows rather than display a fake feeling
  - recommended_team is 'none' on 61.6% of rows -> routing chips only render when real
  - the emerging-cluster rule matches zero rows -> we use mart_trends z-score instead

Rendered by dashboard/app.py as the "Signal Feed" view.
"""
import html
import json
import re

import pandas as pd
import streamlit as st

import db
import theme
import filters as flt
from theme import T, SENT_COLORS
from config import BRAND
from analyze.categories import CATEGORY_LABEL

POS, NEG, NEU = SENT_COLORS["positive"], SENT_COLORS["negative"], SENT_COLORS["neutral"]
CRIT = "#C4544F"

# Reach metrics only exist where Twitter supplied them. Anything ranked by these is
# labelled "tweets" in the UI so we never imply corpus-wide coverage.
REACH_SOURCES = ("twitter",)

# Press/earned media follows a different protocol to customer social: you don't reply to
# an article, you prepare a line for leadership. Mixing them into one feed hides both.
PRESS_SOURCES = ("news", "rssnews", "businessstandard", "gdelt", "hackernews", "youtube")

URG_W = {"critical": 4.0, "high": 3.0, "medium": 1.6, "low": 1.0}
INTENT_W = {"fraud_report": 3.0, "legal_threat": 3.0, "churn_threat": 2.6,
            "complaint": 1.8, "query": 1.2, "suggestion": 1.0,
            "other": 0.8, "praise": 0.3, "spam": 0.1}

INTENT_LABEL = {
    "complaint": "Complaints", "fraud_report": "Fraud reports",
    "churn_threat": "Churn threats", "legal_threat": "Legal threats",
    "query": "Questions", "suggestion": "Suggestions",
    "praise": "Praise", "other": "Other", "spam": "Spam",
}


# ----------------------------------------------------------------- data
@st.cache_data(ttl=1800, show_spinner=False)
def load():
    """Full post records. We join raw_posts+analysis directly rather than use the
    scored_posts view, because that view drops the columns these lanes need
    (sarcasm, confidence, aspects_json, pii_present, conversation_id, engagement detail)."""
    posts = db.df("""
        SELECT r.source_id, r.source, r.author, r.author_name, r.text, r.url,
               r.created_at, r.lang, r.engagement, r.reply_count, r.retweet_count,
               r.quote_count, r.view_count, r.conversation_id,
               a.sentiment, a.score, a.emotion, a.emotion_intensity, a.sarcasm,
               a.intent, a.urgency, a.urgency_reason, a.product, a.root_cause,
               a.recommended_team, a.recommended_action, a.churn_risk, a.fraud_signal,
               a.fraud_type, a.pii_present, a.text_masked, a.pii_types, a.theme,
               a.summary, a.confidence, a.aspects_json, a.model,
               a.issue_category, a.category_reason
        FROM raw_posts r JOIN analysis a ON r.source_id = a.source_id""")
    if not posts.empty:
        posts["created_dt"] = pd.to_datetime(posts["created_at"], errors="coerce",
                                             utc=True, format="mixed")
        for c in ["engagement", "reply_count", "retweet_count", "quote_count",
                  "view_count", "fraud_signal", "churn_risk", "sarcasm", "pii_present"]:
            posts[c] = pd.to_numeric(posts[c], errors="coerce").fillna(0)
        posts["score"] = pd.to_numeric(posts["score"], errors="coerce").fillna(0)
        posts["is_lexicon"] = posts["model"].fillna("").str.contains("vader", case=False)
        posts["reach"] = posts[["engagement", "view_count"]].max(axis=1)

    def _safe(sql):
        try:
            return db.df(sql)
        except Exception:
            return pd.DataFrame()

    # Which posts did the bank actually reply to? Only 4 in the whole corpus — that
    # scarcity is the point of the Response-Gap lane.
    answered = _safe("""SELECT inbound_source_id FROM fact_interaction
                        WHERE n_bank_replies > 0 AND inbound_source_id IS NOT NULL""")
    drafts = _safe("SELECT source_id, draft FROM reply_drafts")
    trends = _safe("SELECT * FROM mart_trends WHERE anomaly = 1 ORDER BY abs(z_score) DESC")
    return posts, answered, drafts, trends


def triage_score(d):
    """urgency x intent x reach x severity. Reach is log-damped so one viral tweet
    can't drown a critical fraud report with no engagement."""
    import numpy as np
    u = d["urgency"].map(URG_W).fillna(1.0)
    i = d["intent"].map(INTENT_W).fillna(0.8)
    reach = np.log1p(d["reach"].clip(lower=0)) / 4.0
    severity = (-d["score"]).clip(lower=0) + 0.35
    flags = 1 + 0.5 * d["fraud_signal"] + 0.3 * d["churn_risk"]
    return (u * i * (1 + reach) * severity * flags).round(2)


# ----------------------------------------------------------------- helpers
def _esc(s, n=None):
    s = str(s or "")
    if n and len(s) > n:
        s = s[:n].rsplit(" ", 1)[0] + "…"
    return html.escape(s)


def _rel(ts):
    if pd.isna(ts):
        return ""
    delta = pd.Timestamp.now(tz="UTC") - ts
    d, h = delta.days, int(delta.total_seconds() // 3600)
    if d > 60:
        return ts.strftime("%d %b %Y")
    if d >= 1:
        return f"{d}d ago"
    return f"{max(h, 0)}h ago"


def _num(n):
    n = float(n or 0)
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return f"{int(n)}"


def _eyebrow(txt, sub=""):
    st.markdown(
        f'<div style="font-family:{T["mono"]};font-size:10px;letter-spacing:.14em;'
        f'text-transform:uppercase;color:var(--muted);margin:24px 0 10px 0;'
        f'padding-bottom:7px;border-bottom:1px solid var(--border)">{txt}'
        + (f'<span style="text-transform:none;letter-spacing:0;margin-left:10px;'
           f'opacity:.75">{sub}</span>' if sub else "")
        + '</div>', unsafe_allow_html=True)


def _chip(label, color=None, solid=False):
    c = color or "var(--muted)"
    style = (f"background:{c};color:#fff;border:1px solid {c}" if solid
             else f"color:{c};border:1px solid {c}")
    return (f'<span style="{style};border-radius:3px;padding:1.5px 7px;font-size:9.5px;'
            f'font-family:{T["mono"]};letter-spacing:.07em;text-transform:uppercase;'
            f'margin-right:5px;white-space:nowrap">{label}</span>')


def _aspects(js):
    try:
        items = json.loads(js) if js else []
    except Exception:
        return []
    return [i for i in items if isinstance(i, dict) and i.get("aspect")]


# ----------------------------------------------------------------- the post card
def post_card(r, draft_map, answered_ids):
    """One post rendered as a decision, not a tweet.

    Layers: raw post -> AI read -> provenance -> recommended action -> evidence.
    A PII-flagged post shows the masked text, never the raw text.
    """
    sent = r["sentiment"] or "neutral"
    col = SENT_COLORS.get(sent, NEU)
    lex = bool(r["is_lexicon"])
    risky = bool(r["fraud_signal"] or r["churn_risk"]
                 or r["intent"] in ("fraud_report", "legal_threat", "churn_threat"))
    border = CRIT if risky else ("var(--border)" if not lex else "var(--border-soft)")

    # PII guard: if the model saw personal data, render the masked variant.
    body = r["text"]
    pii = bool(r["pii_present"])
    if pii and isinstance(r.get("text_masked"), str) and r["text_masked"].strip():
        body = r["text_masked"]

    # header chips
    chips = [_chip(r["source"], T["cement"])]
    if str(r["urgency"]) in ("critical", "high"):
        chips.append(_chip(r["urgency"], CRIT, solid=(r["urgency"] == "critical")))
    if r["intent"] and r["intent"] != "other":
        chips.append(_chip(INTENT_LABEL.get(r["intent"], r["intent"]),
                           CRIT if INTENT_W.get(r["intent"], 0) >= 2.6 else T["amethyst"]))
    if r["fraud_signal"]:
        chips.append(_chip("fraud", CRIT))
    if r["churn_risk"]:
        chips.append(_chip("churn risk", CRIT))
    if r["sarcasm"]:
        chips.append(_chip("sarcasm", T["citrine"]))
    if pii:
        chips.append(_chip("pii masked", T["citrine"]))
    if r["source_id"] not in answered_ids and risky:
        chips.append(_chip("unanswered", T["citrine"]))

    # engagement only where the source actually provides it
    eng = ""
    if r["source"] in REACH_SOURCES and (r["reach"] or r["reply_count"]):
        parts = []
        if r["retweet_count"]:
            parts.append(f"🔁 {_num(r['retweet_count'])}")
        if r["reply_count"]:
            parts.append(f"💬 {_num(r['reply_count'])}")
        if r["view_count"]:
            parts.append(f"👁 {_num(r['view_count'])}")
        elif r["engagement"]:
            parts.append(f"❤ {_num(r['engagement'])}")
        eng = " · ".join(parts)

    # AI read line — emotion suppressed on lexicon rows (all 963 say 'joy', a default)
    read = [f'<b style="color:{col}">{sent.upper()} {r["score"]:+.2f}</b>']
    if not lex and r["emotion"]:
        read.append(f'{_esc(r["emotion"])}')
    if r["product"] and str(r["product"]) not in ("unspecified", "none", "nan"):
        read.append(f'{_esc(r["product"])}')

    conf = r["confidence"] if pd.notna(r["confidence"]) else 0
    prov = (f'lexicon pass · shallow read' if lex
            else f'{_esc(str(r["model"])[:22])} · conf {conf:.2f}')

    st.markdown(f"""
    <div style="background:var(--surface);border:1px solid {border};border-left:3px solid {col};
                border-radius:var(--r);padding:13px 15px;margin-bottom:9px;
                {'opacity:.82' if lex else ''}">
      <div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px">
        <div style="font-size:12px;color:var(--loud)">
          <b>{_esc(r['author_name'] or r['author'] or 'anonymous', 34)}</b>
          <span style="color:var(--muted);font-family:{T['mono']};font-size:10px;
                       margin-left:7px">{_rel(r['created_dt'])}</span>
        </div>
        <div style="font-family:{T['mono']};font-size:10px;color:var(--muted);
                    white-space:nowrap">{eng}</div>
      </div>
      <div style="margin:8px 0 9px 0;font-size:13px;line-height:1.5;color:var(--text)">
        {_esc(body, 420)}
      </div>
      <div style="margin-bottom:8px">{''.join(chips)}</div>
      <div style="font-family:{T['mono']};font-size:10.5px;color:var(--muted);
                  border-top:1px solid var(--border-soft);padding-top:7px">
        {' · '.join(read)}
        <span style="float:right;opacity:.8">{prov}</span>
      </div>
    </div>""", unsafe_allow_html=True)

    # Action + evidence live in an expander so the feed stays scannable.
    action = r.get("recommended_action")
    team = r.get("recommended_team")
    asp = _aspects(r.get("aspects_json"))
    draft = draft_map.get(r["source_id"])
    if (isinstance(action, str) and action.strip()) or asp or draft:
        with st.expander("Action, evidence & draft reply", expanded=False):
            if isinstance(team, str) and team not in ("", "none", "nan"):
                st.markdown(f"**Route to:** `{team}`")
            if isinstance(action, str) and action.strip() and action != "nan":
                st.markdown(f"**Recommended action:** {action}")
            if r.get("urgency_reason") and str(r["urgency_reason"]) != "nan":
                st.caption(f"Why {r['urgency']}: {r['urgency_reason']}")
            for a in asp:
                ev = a.get("evidence") or ""
                st.markdown(
                    f"- **{a.get('aspect','?')}** · *{a.get('sentiment','?')}*"
                    + (f"  \n  > {ev}" if ev else ""))
            if draft:
                st.markdown("**Pre-drafted reply**")
                st.text_area("draft", value=draft, height=110,
                             key=f"nd_{r['source_id']}", label_visibility="collapsed")
                st.caption("Draft only — nothing is posted from this screen.")
            if r["url"]:
                st.markdown(f"[Open original ↗]({r['url']})")


# ----------------------------------------------------------------- highlight rail
def highlight_rail(p, answered_ids):
    tw = p[p["source"].isin(REACH_SOURCES)]
    _eyebrow("Highlight rail", "engagement metrics exist on Twitter only — these rank tweets")

    def tile(label, row, metric, color):
        if row is None or (hasattr(row, "empty") and row.empty):
            return f'<div style="flex:1;min-width:150px">{_card_empty(label)}</div>'
        txt = _esc(row["text"], 92)
        return f"""<div style="flex:1;min-width:150px;background:var(--surface);
             border:1px solid var(--border);border-top:2px solid {color};
             border-radius:var(--r);padding:11px 12px">
          <div style="font-family:{T['mono']};font-size:9px;letter-spacing:.12em;
                      text-transform:uppercase;color:var(--muted)">{label}</div>
          <div style="font-family:{T['display']};font-size:1.15rem;font-weight:600;
                      color:{color};margin:5px 0 3px 0">{metric}</div>
          <div style="font-size:11px;line-height:1.4;color:var(--text);opacity:.85">{txt}</div>
        </div>"""

    def _card_empty(label):
        return (f'<div style="background:var(--surface);border:1px dashed var(--border);'
                f'border-radius:var(--r);padding:11px 12px;height:100%">'
                f'<div style="font-family:{T["mono"]};font-size:9px;letter-spacing:.12em;'
                f'text-transform:uppercase;color:var(--muted)">{label}</div>'
                f'<div style="font-size:11px;color:var(--muted);margin-top:6px">no data</div></div>')

    def top(df, col, asc=False):
        d = df.dropna(subset=[col])
        d = d[d[col] != 0] if col != "score" else d
        return None if d.empty else d.sort_values(col, ascending=asc).iloc[0]

    tiles = [
        tile("Most reshared", top(tw, "retweet_count"),
             _num(top(tw, "retweet_count")["retweet_count"]) if top(tw, "retweet_count") is not None else "—", T["amethyst"]),
        tile("Most traction", top(tw, "view_count"),
             _num(top(tw, "view_count")["view_count"]) if top(tw, "view_count") is not None else "—", T["minsk"]),
        tile("Most replied", top(tw, "reply_count"),
             _num(top(tw, "reply_count")["reply_count"]) if top(tw, "reply_count") is not None else "—", T["citrine"]),
        tile("Top positive", top(p, "score"),
             f'{top(p, "score")["score"]:+.2f}' if top(p, "score") is not None else "—", POS),
        tile("Top negative", top(p, "score", asc=True),
             f'{top(p, "score", asc=True)["score"]:+.2f}' if top(p, "score", asc=True) is not None else "—", NEG),
    ]
    st.markdown(f'<div style="display:flex;gap:9px;flex-wrap:wrap">{"".join(tiles)}</div>',
                unsafe_allow_html=True)


# ----------------------------------------------------------------- lanes
LANES = {
    "🗞 All posts": "all",
    "🚨 Response-gap war room": "gap",
    "🛑 Red-flag desk": "risk",
    "🛡 Fraud fast-lane": "fraud",
    "💬 Unanswered questions": "query",
    "🎭 Sarcasm & model catches": "sarcasm",
    "🔒 PII exposure": "pii",
    "📰 Press coverage": "press",
}


def apply_lane(p, lane, answered_ids):
    """Returns (filtered_df, caption). Each lane is one of the vetted insight views."""
    if lane == "gap":
        d = p[(~p["source_id"].isin(answered_ids))
              & ((p["urgency"].isin(["critical", "high"]))
                 | (p["intent"].isin(["fraud_report", "legal_threat", "churn_threat"]))
                 | (p["fraud_signal"] == 1))
              & (p["score"] < 0)]
        return d, ("High-stakes negative posts with no detected bank reply. Only 4 replies "
                   "exist in the entire corpus, so treat this as a backlog, not a live SLA clock.")
    if lane == "risk":
        d = p[(p["fraud_signal"] == 1) | (p["churn_risk"] == 1)
              | (p["intent"].isin(["fraud_report", "legal_threat", "churn_threat"]))]
        return d, ("The liability set — fraud, churn and legal exposure fused into one lane, "
                   "separated from the 1,046 praise posts.")
    if lane == "fraud":
        d = p[p["fraud_signal"] == 1]
        return d, ("Posts the model flagged as fraud-related. Personal data is masked on any "
                   "card tagged `pii masked`. Note the keyword pass over-triggers on words "
                   "like 'hack' and 'kyc' — verify before escalating.")
    if lane == "query":
        d = p[(p["intent"] == "query") & (~p["source_id"].isin(answered_ids))]
        return d, ("Customers asking questions, not complaining, that nobody answered. "
                   "Low-effort goodwill — no liability attached.")
    if lane == "sarcasm":
        d = p[(p["sarcasm"] == 1) | ((~p["is_lexicon"]) & (p["score"] < -0.5)
                                     & (p["sentiment"] == "negative"))]
        return d, ("Posts a keyword tool would score backwards. Sarcasm detection is only "
                   "possible on LLM-read rows — the lexicon pass cannot flag it at all.")
    if lane == "pii":
        d = p[p["pii_present"] == 1]
        return d, ("Customers who publicly exposed their own Aadhaar / PAN / account details. "
                   "Reach out and get it taken down. Text is always shown masked.")
    if lane == "press":
        d = p[p["source"].isin(PRESS_SOURCES)]
        return d, ("Earned media, separated from customer social. You don't reply to an "
                   "article — you brief leadership. These carry no engagement metrics, so "
                   "they rank by recency and sentiment, never reach.")
    return p, ""


# ----------------------------------------------------------------- thread reconstructor
def threads_panel(p, answered_ids):
    """A single post lies about context. Rebuild the conversation and you see the
    trajectory — where it started, who piled on, whether the bank ever showed up."""
    t = p[p["conversation_id"].notna() & (p["conversation_id"].astype(str) != "")]
    if t.empty:
        return
    g = (t.groupby("conversation_id")
           .agg(posts=("source_id", "size"), authors=("author", "nunique"),
                avg=("score", "mean"), worst=("score", "min"))
           .reset_index())
    g = g[g["posts"] > 1].sort_values("posts", ascending=False)
    if g.empty:
        return

    with st.expander(f"🧵 Thread reconstructor — {len(g)} multi-post conversations", False):
        st.caption(
            "Author-diversity separates genuine mass outrage from one person reposting: "
            "many distinct authors on one thread is a real pile-on. Only Twitter and Reddit "
            "carry a conversation id, so most of the corpus has no thread to rebuild.")
        g["kind"] = ["brigade / mass outrage" if a >= 0.7 * n else "single-actor repetition"
                     for n, a in zip(g["posts"], g["authors"])]
        top = g.head(12)
        pick = st.selectbox(
            "Conversation",
            top["conversation_id"].tolist(),
            format_func=lambda c: (
                f"{int(top.loc[top.conversation_id==c,'posts'].iloc[0])} posts · "
                f"{int(top.loc[top.conversation_id==c,'authors'].iloc[0])} authors · "
                f"{top.loc[top.conversation_id==c,'kind'].iloc[0]}"),
            key="nr_thread")
        th = t[t["conversation_id"] == pick].sort_values("created_dt", na_position="last")
        row = g[g["conversation_id"] == pick].iloc[0]
        k = st.columns(4)
        k[0].metric("Posts", int(row["posts"]))
        k[1].metric("Distinct authors", int(row["authors"]))
        k[2].metric("Avg sentiment", f"{row['avg']:+.2f}")
        k[3].metric("Worst post", f"{row['worst']:+.2f}")
        if row["authors"] >= 0.7 * row["posts"]:
            st.warning("Many distinct authors — genuine pile-on, not one account repeating.")
        else:
            st.info("Few authors relative to posts — largely one account repeating itself.")
        replied = th["source_id"].isin(answered_ids).any()
        st.caption("Bank reply detected in this thread." if replied
                   else "No bank reply detected anywhere in this thread.")
        for _, r in th.iterrows():
            c = SENT_COLORS.get(r["sentiment"], NEU)
            st.markdown(
                f'<div style="border-left:3px solid {c};padding:5px 0 5px 11px;margin-bottom:6px">'
                f'<span style="font-family:{T["mono"]};font-size:10px;color:var(--muted)">'
                f'{_esc(r["author"] or "anon", 24)} · {_rel(r["created_dt"])} · '
                f'<b style="color:{c}">{r["score"]:+.2f}</b></span><br>'
                f'<span style="font-size:12px">{_esc(r["text"], 220)}</span></div>',
                unsafe_allow_html=True)


# ----------------------------------------------------------------- influencer watchtower
def influencer_panel():
    """PR triages by blast radius. The real value here is the mismatch flag: a high-reach
    account whose aggregate score reads positive while its worst post is scathing."""
    try:
        inf = db.df("""SELECT author, author_name, reach, mentions, avg_score, stance,
                              worst_summary, url FROM mart_influencers
                       ORDER BY reach DESC LIMIT 25""")
    except Exception:
        return
    if inf.empty:
        return
    with st.expander(f"📣 Influencer watchtower — top {len(inf)} by reach", False):
        st.caption(
            "Ranked by blast radius, not volume. A CHECK flag means the account's average "
            "score reads positive while its worst post is scathing — the aggregate is hiding "
            "the risk. Reach exists only where Twitter supplied it.")
        show = inf.copy()
        show["flag"] = ["⚠️ CHECK" if (s == "positive" and isinstance(w, str) and w.strip())
                        else "" for s, w in zip(show["stance"], show["worst_summary"])]
        show = show.rename(columns={"author": "Handle", "reach": "Reach",
                                    "mentions": "Posts", "avg_score": "Avg score",
                                    "stance": "Stance", "worst_summary": "Worst post",
                                    "flag": ""})
        st.dataframe(show[["", "Handle", "Reach", "Posts", "Avg score", "Stance", "Worst post"]],
                     width="stretch", hide_index=True,
                     column_config={"Avg score": st.column_config.NumberColumn(format="%.3f"),
                                    "Reach": st.column_config.NumberColumn(format="%d")})


# ----------------------------------------------------------------- entry point
def render():
    posts, answered, drafts, trends = load()
    if posts.empty:
        st.warning("No analysed posts yet. Run the pipeline, then reload.")
        return

    answered_ids = set(answered["inbound_source_id"].dropna()) if not answered.empty else set()
    draft_map = dict(zip(drafts["source_id"], drafts["draft"])) if not drafts.empty else {}

    # ---- masthead
    st.markdown(f"""
    <div style="display:flex;justify-content:space-between;align-items:flex-end;
                border-bottom:1px solid var(--border);padding-bottom:13px;margin-bottom:4px">
      <div>
        <div style="font-family:{T['mono']};font-size:10px;letter-spacing:.16em;
                    text-transform:uppercase;color:var(--muted);margin-bottom:5px">
          Signal feed · what customers are actually saying</div>
        <div style="font-family:{T['display']};font-size:1.6rem;font-weight:600;
                    color:var(--loud);letter-spacing:-.028em">{BRAND}</div>
      </div>
      <div style="text-align:right;font-family:{T['mono']};font-size:10.5px;color:var(--muted)">
        {len(posts):,} analysed posts
      </div>
    </div>""", unsafe_allow_html=True)

    # ---- anomaly rail (replaces the emerging-cluster banner, which matches zero rows)
    if not trends.empty:
        t = trends.iloc[0]
        st.info(f"**Spike detected — {t['category']}** · {int(t['mentions'])} mentions on "
                f"{t['day']} · z-score {t['z_score']:+.2f} · avg sentiment {t['avg_score']:+.2f}")

    highlight_rail(posts, answered_ids)

    # ---- category comparison: what each issue category actually looks like
    # Grouped on issue_category, not intent. The panel was always titled "issue
    # category" but grouped by intent, which conflated what a post DOES
    # (complain) with what it is ABOUT (the branch).
    _eyebrow("Post by issue category", "volume, tone and severity per category")
    cats = (posts.assign(issue_category=posts["issue_category"].fillna("other"))
            .groupby("issue_category")
            .agg(posts=("source_id", "size"), avg=("score", "mean"),
                 crit=("urgency", lambda s: int((s == "critical").sum())),
                 fraud=("fraud_signal", "sum"))
            .reset_index().sort_values("posts", ascending=False))
    cards = []
    for _, c in cats.iterrows():
        col = POS if c["avg"] > 0.1 else NEG if c["avg"] < -0.1 else NEU
        badge = (f'<span style="color:{CRIT};font-family:{T["mono"]};font-size:9px">'
                 f'{int(c["crit"])} critical</span>' if c["crit"] else
                 (f'<span style="color:{CRIT};font-family:{T["mono"]};font-size:9px">'
                  f'{int(c["fraud"])} fraud</span>' if c["fraud"] else
                  f'<span style="font-family:{T["mono"]};font-size:9px;color:var(--muted)">—</span>'))
        cards.append(f"""<div style="flex:1;min-width:118px;background:var(--surface);
            border:1px solid var(--border);border-top:2px solid {col};border-radius:var(--r);
            padding:9px 11px">
          <div style="font-family:{T['mono']};font-size:9px;letter-spacing:.1em;
                      text-transform:uppercase;color:var(--muted)">
            {CATEGORY_LABEL.get(c['issue_category'], c['issue_category'])}</div>
          <div style="font-family:{T['display']};font-size:1.25rem;font-weight:600;
                      color:var(--loud);margin:3px 0">{int(c['posts']):,}</div>
          <div style="font-family:{T['mono']};font-size:10px;color:{col}">{c['avg']:+.2f}</div>
          <div style="margin-top:3px">{badge}</div>
        </div>""")
    st.markdown(f'<div style="display:flex;gap:8px;flex-wrap:wrap">{"".join(cards)}</div>',
                unsafe_allow_html=True)

    # ---- cross-cutting panels
    st.write("")
    threads_panel(posts, answered_ids)
    influencer_panel()


    # ---- controls
    _eyebrow("Filter section")
    c = st.columns([0.26, 0.22, 0.22, 0.3])
    lane_label = c[0].selectbox("Lane", list(LANES), key="nr_lane",
                                label_visibility="collapsed")

    months = flt.month_options(posts)
    month_pick = c[1].selectbox("Month", ["All months"] + [m for m, _ in months],
                                key="nr_month", label_visibility="collapsed")
    cat_opts = ["All categories"] + [CATEGORY_LABEL.get(k, k) for k in
                                     sorted(posts["issue_category"].dropna().unique().tolist())]
    cat = c[2].selectbox("Category", cat_opts, key="nr_cat", label_visibility="collapsed")
    q = c[3].text_input("Search", key="nr_q", placeholder="Search text…",
                        label_visibility="collapsed")

    d, caption = apply_lane(posts, LANES[lane_label], answered_ids)
    if month_pick != "All months":
        d = flt.apply_month(d, dict(months)[month_pick])
    if cat != "All categories":
        inv = {v: k for k, v in CATEGORY_LABEL.items()}
        d = d[d["issue_category"] == inv.get(cat, cat)]
    if q:
        d = d[d["text"].str.contains(q, case=False, na=False, regex=False)]

    # ---- stacked conditions (the e-commerce-style builder)
    with st.expander("More filters", expanded=bool(st.session_state.get("nr_filters"))):
        active = flt.filter_builder(d)
    d = flt.apply_all(d, active)
    if active:
        chips = " · ".join(x for x in (flt.describe(f) for f in active) if x)
        if chips:
            st.caption(f"Filters: {chips}")

    if caption:
        st.caption(caption)

    # ---- lane analytics strip
    if not d.empty:
        m = st.columns(5)
        m[0].metric("Posts", f"{len(d):,}")
        m[1].metric("Avg sentiment", f"{d['score'].mean():+.2f}")
        m[2].metric("Critical", int((d["urgency"] == "critical").sum()))
        m[3].metric("Fraud flags", int(d["fraud_signal"].sum()))
        deep = int((~d["is_lexicon"]).sum())
        m[4].metric("Deep-read", f"{deep}/{len(d)}",
                    help="Rows read by the LLM. The rest are lexicon-only — shallower, "
                         "and shown dimmed with no emotion.")

    # ---- sort + view
    # Feed only. The spreadsheet lives on its own surface (dashboard/explorer.py),
    # which has room for every column instead of a squeezed subset.
    s = st.columns([0.3, 0.22, 0.48])
    sort_field = s[0].selectbox("Sort by", list(flt.SORT_FIELDS), key="nr_sortf",
                                label_visibility="collapsed")
    direction = s[1].radio("Order", ["Desc", "Asc"], horizontal=True, key="nr_dir",
                           label_visibility="collapsed")

    asc = direction == "Asc"
    col = flt.SORT_FIELDS[sort_field]
    if col is None:                       # triage priority is computed, not stored
        d = d.assign(_p=triage_score(d)).sort_values("_p", ascending=asc)
    else:
        d = d.sort_values(col, ascending=asc, na_position="last")

    _eyebrow("The feed", f"{len(d):,} posts")
    if d.empty:
        st.info("No posts match this lane and filter combination.")
        return

    PAGE = 25
    n = st.session_state.get("nr_n", PAGE)
    for _, r in d.head(n).iterrows():
        post_card(r, draft_map, answered_ids)

    if n < len(d):
        if st.button(f"Load {min(PAGE, len(d)-n)} more  ({n} of {len(d):,} shown)"):
            st.session_state["nr_n"] = n + PAGE
            st.rerun()
