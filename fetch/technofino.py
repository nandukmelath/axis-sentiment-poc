"""Technofino community forum (technofino.in) — India's credit-card enthusiast forum and
the epicenter of Axis rewards/devaluation discussion (Magnus, Atlas, Airtel CC threads).
Keyless, two tiers (endpoints + selectors verified live 2026-07-07):

  TIER 1  XenForo RSS — Axis subforum thread feed + global new-threads firehose
          (brand-filtered). Each item = thread title + FULL first post (content:encoded).
  TIER 2  Thread-page deep-crawl (server-rendered) — replies carry the densest sentiment
          ("they basically killed the card"). bs4 selectors: article.message--post,
          data-author, time[itemprop=datePublished], .message-body .bbWrapper.

robots.txt: generic UA allowed; Content-Signal ai-train=no (we only *reference* content
for sentiment — no model training). Throttled ~2.5s/page to stay polite under Cloudflare.
"""
import re, time, datetime
import feedparser
from bs4 import BeautifulSoup
from config import FETCH_LIMITS, TECHNOFINO_DEEP_THREADS, TECHNOFINO_REPLIES
from fetch.webutil import get, brand_match

AXIS_FORUM_RSS = "https://www.technofino.in/community/forums/axis-bank-credit-card.45/index.rss"
GLOBAL_RSS = "https://www.technofino.in/community/forums/-/index.rss"


def _first_post_text(entry):
    """Thread title + first-post body from content:encoded (falls back to summary)."""
    html = ""
    if entry.get("content"):
        html = entry["content"][0].get("value", "")
    body = BeautifulSoup(html or entry.get("summary", ""), "html.parser").get_text(" ", strip=True)
    return f"{entry.get('title', '')}\n{body}".strip()


def _thread_id(entry):
    m = re.search(r"\.(\d+)/?(?:\?.*)?$", entry.get("link", ""))
    return (entry.get("id") or "").strip() if not m else m.group(1)


def _rss_rows(url, require_brand):
    d = feedparser.parse(url)
    rows = []
    for e in d.entries:
        text = _first_post_text(e)
        if require_brand and not brand_match(text):
            continue
        tid = _thread_id(e)
        if not tid:
            continue
        rows.append(dict(
            source_id=f"technofino:{tid}", source="technofino",
            author=e.get("author", ""), author_name=e.get("author", ""),
            text=text[:4000], url=(e.get("link") or "").split("?")[0],
            created_at=e.get("published", ""), engagement=0, lang="en",
            conversation_id=f"tf_{tid}"))
    return rows


def _reaction_count(art):
    bar = art.select_one(".reactionsBar-link")
    if not bar:
        return 0
    txt = bar.get_text(" ", strip=True)
    m = re.search(r"and (\d+) others?", txt)
    extra = int(m.group(1)) if m else 0
    names = txt.split(" and ")[0]
    return extra + max(1, names.count(",") + 1)


def _crawl_replies(thread_url, tid, limit):
    """Parse reply posts from a server-rendered thread page (first page)."""
    try:
        r = get(thread_url)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:
        return []
    out = []
    for art in soup.select("article.message--post")[1:1 + limit]:   # [0] = the first post (already in RSS)
        body = art.select_one(".message-body .bbWrapper")
        if not body:
            continue
        text = body.get_text(" ", strip=True)
        if len(text) < 15:
            continue
        pid = (art.get("id") or "").replace("js-post-", "")
        t = art.select_one("time[itemprop=datePublished]")
        out.append(dict(
            source_id=f"technofino:p_{pid or hash(text) & 0xffffffff}", source="technofino",
            author=art.get("data-author", ""), author_name=art.get("data-author", ""),
            text=text[:4000], url=thread_url,
            created_at=(t.get("datetime") if t else ""), engagement=_reaction_count(art),
            lang="en", conversation_id=f"tf_{tid}"))
    return out


def fetch():
    rows = {}
    try:
        for r in _rss_rows(AXIS_FORUM_RSS, require_brand=False):   # whole subforum IS Axis
            rows[r["source_id"]] = r
        for r in _rss_rows(GLOBAL_RSS, require_brand=True):        # catch Axis threads elsewhere
            rows.setdefault(r["source_id"], r)
    except Exception as e:
        print(f"  [technofino] rss error: {str(e)[:80]}")

    # Tier 2 — deep-crawl replies for the freshest Axis-subforum threads
    threads = [r for r in rows.values() if not r["source_id"].startswith("technofino:p_")]
    for r in threads[:TECHNOFINO_DEEP_THREADS]:
        tid = r["conversation_id"].removeprefix("tf_")
        for c in _crawl_replies(r["url"], tid, TECHNOFINO_REPLIES):
            rows.setdefault(c["source_id"], c)
        time.sleep(2.5)   # polite under Cloudflare

    out = list(rows.values())[: max(FETCH_LIMITS.get("technofino", 40), 40)]
    n_replies = sum(1 for r in out if r["source_id"].startswith("technofino:p_"))
    print(f"  [technofino] {len(out)} ({len(out) - n_replies} threads + {n_replies} replies)")
    return out


if __name__ == "__main__":
    from db import init_db, upsert_posts
    init_db()
    rs = fetch()
    upsert_posts(rs)
    print(f"landed {len(rs)} -> raw_posts")
