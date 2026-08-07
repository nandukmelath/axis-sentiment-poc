"""Google News RSS — free, no key. Multiple queries for wider coverage."""
import hashlib
import urllib.parse
import feedparser
from bs4 import BeautifulSoup
from config import NEWS_QUERIES, FETCH_LIMITS
from fetch.webutil import brand_match

RSS = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"


def fetch():
    per = max(3, FETCH_LIMITS["news"] // max(1, len(NEWS_QUERIES)))
    rows = {}
    for query in NEWS_QUERIES:
        q = urllib.parse.quote(f'"{query}"' if " " in query else query)
        d = feedparser.parse(RSS.format(q=q))
        for e in d.entries[:per]:
            # Google News `summary` is an HTML blob (redirect <a> carrying a ~250-char
            # base64 CBMi... token, &nbsp;, <font> outlet name) — ~70% of chars were markup,
            # which the classifier paid tokens for and the embeddings clustered on. Strip it,
            # same as fetch/rss_news.py already does.
            summary = BeautifulSoup(e.get("summary", ""), "html.parser").get_text(" ", strip=True)
            text = f"{e.get('title','')}. {summary}".strip()
            if not brand_match(text):        # Google News fuzzy-matches; drop off-brand hits
                continue
            sid = "news:" + hashlib.md5(e.get("link", text).encode(), usedforsecurity=False).hexdigest()[:12]
            rows[sid] = dict(
                source_id=sid, source="news", author=e.get("source", {}).get("title", "news"),
                text=text, url=e.get("link", ""), created_at=e.get("published", ""),
                engagement=0, lang="en")
    print(f"  [news] {len(rows)} ({len(NEWS_QUERIES)} queries)")
    return list(rows.values())
