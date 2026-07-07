"""Direct-outlet Indian banking-desk RSS pack — keyless, complements Google News RSS with
banking-vertical coverage (regulator moves, exec churn, product changes). Feeds verified
live 2026-07-07; two traps found and handled:
  - Moneycontrol business.xml returns HTTP 200 with a feed FROZEN at Apr-2024 -> staleness
    guard drops any feed whose newest item is older than MAX_AGE_DAYS.
  - ETBFSI's documented /rss/banking returns an empty channel -> use topstories/recentstories.
Business-Standard is behind a hard 403 bot wall -> excluded.
Only brand-matching items are ingested (banking-desk feeds: 'axis' ~= Axis Bank)."""
import time, hashlib, datetime, calendar
import feedparser
from bs4 import BeautifulSoup
from config import FETCH_LIMITS, RSS_NEWS_FEEDS, RSS_NEWS_MAX_AGE_DAYS
from fetch.webutil import brand_match

MAX_AGE_DAYS = RSS_NEWS_MAX_AGE_DAYS


def _fresh(d):
    """Reject frozen feeds (the Moneycontrol trap): newest entry must be recent."""
    newest = 0
    for e in d.entries:
        tp = e.get("published_parsed") or e.get("updated_parsed")
        if tp:
            newest = max(newest, calendar.timegm(tp))
    if not newest:
        return False
    age = (time.time() - newest) / 86400
    return age <= MAX_AGE_DAYS


def _iso(e):
    tp = e.get("published_parsed") or e.get("updated_parsed")
    if not tp:
        return e.get("published", "")
    return datetime.datetime.fromtimestamp(calendar.timegm(tp), datetime.timezone.utc).isoformat()


def _rows(brand_label, d, per):
    rows = []
    for e in d.entries[:per * 6]:          # scan deeper than we keep — brand filter is sparse
        summary = BeautifulSoup(e.get("summary", ""), "html.parser").get_text(" ", strip=True)
        text = f"{e.get('title', '')}. {summary}".strip()
        if not brand_match(text):
            continue
        key = e.get("id") or e.get("link") or text
        sid = "rssnews:" + hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()[:12]
        rows.append(dict(
            source_id=sid, source="rssnews", author=brand_label,
            text=text[:4000], url=e.get("link", ""), created_at=_iso(e),
            engagement=0, lang="en"))
        if len(rows) >= per:
            break
    return rows


def fetch():
    per = max(5, FETCH_LIMITS.get("rssnews", 40) // max(1, len(RSS_NEWS_FEEDS)))
    rows = {}
    for label, url in RSS_NEWS_FEEDS:
        try:
            d = feedparser.parse(url)
            if not d.entries:
                print(f"  [rssnews] {label}: empty feed — skipped")
                continue
            if not _fresh(d):
                print(f"  [rssnews] {label}: STALE feed (newest item > {MAX_AGE_DAYS}d) — skipped")
                continue
            for r in _rows(label, d, per):
                rows.setdefault(r["source_id"], r)
        except Exception as e:
            print(f"  [rssnews] {label} error: {str(e)[:70]}")
    print(f"  [rssnews] {len(rows)} ({len(RSS_NEWS_FEEDS)} feeds, brand-filtered)")
    return list(rows.values())


if __name__ == "__main__":
    from db import init_db, upsert_posts
    init_db()
    rs = fetch()
    upsert_posts(rs)
    print(f"landed {len(rs)} -> raw_posts")
