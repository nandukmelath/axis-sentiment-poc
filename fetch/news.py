"""Google News RSS — free, no key. Multiple queries for wider coverage."""
import hashlib
import urllib.parse
import feedparser
from config import NEWS_QUERIES, FETCH_LIMITS

RSS = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"


def fetch():
    per = max(3, FETCH_LIMITS["news"] // max(1, len(NEWS_QUERIES)))
    rows = {}
    for query in NEWS_QUERIES:
        q = urllib.parse.quote(f'"{query}"' if " " in query else query)
        d = feedparser.parse(RSS.format(q=q))
        for e in d.entries[:per]:
            text = f"{e.get('title','')}. {e.get('summary','')}"
            sid = "news:" + hashlib.md5(e.get("link", text).encode()).hexdigest()[:12]
            rows[sid] = dict(
                source_id=sid, source="news", author=e.get("source", {}).get("title", "news"),
                text=text, url=e.get("link", ""), created_at=e.get("published", ""),
                engagement=0, lang="en")
    print(f"  [news] {len(rows)} ({len(NEWS_QUERIES)} queries)")
    return list(rows.values())
