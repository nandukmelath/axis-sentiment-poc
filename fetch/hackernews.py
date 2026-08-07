"""Hacker News via the Algolia search API — FREE, no key. Stories + comments mentioning the brand.

WHY THIS MODULE USED TO RETURN 0 (fixed 2026-08-05)
Algolia's default matching is LOOSE: it ORs the tokens, prefix-matches the last one and
applies typo tolerance. The old code sent the bare string "Axis Bank" with no advancedSyntax
flag to /search_by_date, so Algolia matched ~3.1k records containing merely `axis` or `bank`
(nbHits=3086 on probe) and /search_by_date then sorted strictly by RECENCY — so page 1 was
always the 30 NEWEST unrelated HN posts. The brand filter was the only thing working: it
correctly discarded 30/30 off-brand hits, every run.

THE FIX IS IN THE QUERY, NOT THE FILTER
  1. Quote the brand as a phrase AND send advancedSyntax=true — without that flag Algolia
     ignores the quotes. '"Axis Bank"' => nbHits 11 (all genuine) instead of 3086.
  2. Rank by RELEVANCE (/search), not recency. Axis Bank surfaces on HN roughly once a year,
     so recency-only ordering is the wrong tool; /search_by_date is used only as a second
     pass when a query has more hits than one page.
  3. Search stories AND comments, and widen past "Axis Bank" to the brand's other terms.
Algolia will NOT OR a phrase with a bare token in one request — '"Axis Bank" OR axisbank'
returns 0 hits — so every term is issued as its own query and the hits are merged by objectID.

GROUND TRUTH (live probe 2026-08-05): HN's entire 2007-2026 corpus holds ~12 genuine Axis Bank
items ('"Axis Bank"' nbHits=11, 'axisbank' nbHits=27 of which 12 survive the brand gate).
That is a real, low-volume source — not a broken one. Newest genuine item: 2026-01-10.
"""
import datetime
import html as _html
import os
import re
import requests
import config
from config import HN_QUERY, FETCH_LIMITS

BASE = "https://hn.algolia.com/api/v1"
TAG_RE = re.compile(r"<[^>]+>")
NON_ALNUM = re.compile(r"[^a-z0-9]+")
PAGE = 100          # Algolia allows up to 1000; the whole on-brand corpus is <50, so one page is plenty

# Each entry is its own Algolia request. Phrases MUST stay quoted (see module docstring).
# Precedence: config.HN_QUERIES (str ';'-separated or list) -> env HN_QUERIES -> default below.
# The getattr keeps this working today AND if the orchestrator later adds the config constant.
_DEFAULT_QUERIES = f'"{HN_QUERY}";axisbank;"Axis Magnus";"Axis Securities"'
_raw = getattr(config, "HN_QUERIES", None) or os.getenv("HN_QUERIES") or _DEFAULT_QUERIES
QUERIES = [str(q).strip() for q in
           (_raw if isinstance(_raw, (list, tuple)) else str(_raw).split(";")) if str(q).strip()]

# Precision gate (this module's brand filter), applied to a punctuation-normalised haystack
# (title + story + comment + URL) so "axis-bank-and-sbi-hacks" in a URL and "AxisBank" both match.
# Deliberately NOT fetch/webutil.brand_match: its \baxis\b requires `axis` as a standalone word,
# so it rejects the one-word "axisbank" form and drops genuine axisbank.com stories (verified
# on HN 9719456). This regex is stricter than \baxis\b on every other axis, and catches that too.
# `axisdirect` deliberately allows NO space: "two axis direct-drive mount" is a real HN hit.
BRAND_RE = re.compile(
    r"\b(axis\s?bank|axis\s?magnus|axisdirect|axis\s?securities|axis\s?max\s?life)\b")


def _clean(raw):
    """Strip Algolia's HTML and decode entities (&#x27; &#x2F; &amp; ...)."""
    return _html.unescape(TAG_RE.sub("", raw or "")).strip()


def _int(v):
    """Never let one malformed Algolia field discard the whole batch."""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _iso(h):
    """Algolia already emits ISO-8601 UTC ('2026-01-10T05:08:10Z'); rebuild from epoch if absent."""
    s = h.get("created_at") or ""
    if s:
        return s
    ts = h.get("created_at_i")
    if ts:
        return (datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc)
                .isoformat().replace("+00:00", "Z"))
    return ""


def _haystack(h):
    """Everything the brand could appear in, punctuation-normalised and lowercased."""
    raw = " ".join(str(h.get(k) or "") for k in
                   ("title", "story_title", "story_text", "comment_text", "url"))
    return NON_ALNUM.sub(" ", TAG_RE.sub(" ", raw).lower())


def _search(endpoint, query):
    """One Algolia call. Returns (hits, nbHits). advancedSyntax is what honours the quotes."""
    r = requests.get(f"{BASE}/{endpoint}", timeout=25, params={
        "query": query, "tags": "(story,comment)", "hitsPerPage": PAGE, "advancedSyntax": "true"})
    if r.status_code != 200:
        raise RuntimeError(f"{endpoint} HTTP {r.status_code}")
    j = r.json()
    return j.get("hits") or [], int(j.get("nbHits") or 0)


def _fetch():
    n = FETCH_LIMITS.get("hackernews", 30)
    hits, errs = {}, []
    for q in QUERIES:
        try:
            page, nb = _search("search", q)                 # relevance-ranked: the right tool here
            for h in page:
                hits[str(h.get("objectID"))] = h
            if nb > len(page):                              # >1 page exists — also take the newest slice
                page2, _ = _search("search_by_date", q)
                for h in page2:
                    hits[str(h.get("objectID"))] = h
        except Exception as e:
            errs.append(f"{q}={str(e)[:40]}")
    if errs and not hits:
        print(f"  [hackernews] error: {'; '.join(errs)[:110]}")
        return []

    out = []
    for oid, h in hits.items():
        if not BRAND_RE.search(_haystack(h)):             # brand filter — off-topic never lands
            continue
        title = _clean(h.get("title"))
        body = _clean(h.get("comment_text") or h.get("story_text"))
        text = "\n".join(p for p in (title, body) if p) or _clean(h.get("story_title"))
        if not text:                                        # contract: text must be non-empty
            continue
        out.append(dict(
            source_id=f"hackernews:{oid}", source="hackernews", author=h.get("author") or "",
            text=text, url=h.get("url") or f"https://news.ycombinator.com/item?id={oid}",
            created_at=_iso(h), engagement=_int(h.get("points")),
            reply_count=_int(h.get("num_comments")),
            conversation_id=str(h.get("story_id") or oid), lang="en"))
    out.sort(key=lambda r: r["created_at"], reverse=True)    # ISO-8601 sorts lexicographically
    out = out[:n]
    if errs:
        print(f"  [hackernews] partial ({len(errs)} query error(s)): {'; '.join(errs)[:70]}")
    print(f"  [hackernews] {len(out)}")
    return out


def fetch():
    """Contract: no args, never raises, returns list[dict] with keys ⊆ db.RAW_COLS."""
    try:
        return _fetch()
    except Exception as e:
        print(f"  [hackernews] error: {str(e)[:100]}")
        return []
