"""Competitor mention fetch + Share-of-Voice (KEYLESS).
Pulls Google News for each competitor (HDFC/ICICI/SBI/Kotak), VADER-scores, lands in
competitor_posts, then rebuilds mart_competitor_sov (Axis vs rivals).

Run:  python -m analytics.competitor
"""
import hashlib
import urllib.parse
import feedparser

import db
from config import COMPETITORS
from analyze.cascade import classify_fast

NEWS = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
COMP_COLS = ["source_id", "brand", "source", "author", "text", "url", "created_at",
             "sentiment", "score", "fetched_at"]


def fetch_brand(brand, limit=15):
    rows = {}
    d = feedparser.parse(NEWS.format(q=urllib.parse.quote(f'"{brand}"')))
    for e in d.entries[:limit]:
        text = f"{e.get('title', '')}. {e.get('summary', '')}"
        sid = "comp:" + hashlib.md5((brand + (e.get("link") or text)).encode()).hexdigest()[:12]
        f = classify_fast(text)
        rows[sid] = dict(source_id=sid, brand=brand, source="news",
                         author=e.get("source", {}).get("title", "news"), text=text,
                         url=e.get("link", ""), created_at=e.get("published", ""),
                         sentiment=f["sentiment"], score=f["score"], fetched_at=db.now())
    return list(rows.values())


def run(limit=15):
    from analytics.features import ensure_tables, build_competitor_sov
    ensure_tables()
    total = 0
    for brand in COMPETITORS:
        try:
            rows = fetch_brand(brand, limit)
        except Exception as e:
            print(f"  [{brand}] error: {str(e)[:80]}")
            continue
        db.upsert_rows("competitor_posts", rows, "source_id", COMP_COLS)
        total += len(rows)
        print(f"  [{brand}] {len(rows)}")
    build_competitor_sov()
    print(f"competitor mentions: {total}")
    return total


if __name__ == "__main__":
    run()
