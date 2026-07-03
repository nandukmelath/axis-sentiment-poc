"""Hacker News via the Algolia search API — FREE, no key. Stories + comments mentioning the brand."""
import re
import requests
from config import HN_QUERY, FETCH_LIMITS

API = "http://hn.algolia.com/api/v1/search_by_date"
TAG_RE = re.compile(r"<[^>]+>")
MENTIONS = ["axis bank", "axisbank", "axis magnus"]


def _clean(html):
    return TAG_RE.sub("", html or "").replace("&#x27;", "'").replace("&quot;", '"').replace("&amp;", "&").strip()


def fetch():
    n = FETCH_LIMITS.get("hackernews", 30)
    try:
        r = requests.get(API, params={"query": HN_QUERY, "tags": "(story,comment)", "hitsPerPage": n}, timeout=25)
        hits = r.json().get("hits", []) if r.status_code == 200 else []
    except Exception as e:
        print(f"  [hackernews] error: {str(e)[:80]}")
        return []
    out = []
    for h in hits:
        text = _clean(h.get("title") or h.get("story_text") or h.get("comment_text") or "")
        if not any(m in text.lower() for m in MENTIONS):     # Algolia is loose — keep true mentions
            continue
        oid = h.get("objectID")
        out.append(dict(
            source_id=f"hackernews:{oid}", source="hackernews", author=h.get("author", ""),
            text=text, url=h.get("url") or f"https://news.ycombinator.com/item?id={oid}",
            created_at=h.get("created_at", ""), engagement=int(h.get("points") or 0),
            reply_count=int(h.get("num_comments") or 0), lang="en"))
    print(f"  [hackernews] {len(out)}")
    return out
