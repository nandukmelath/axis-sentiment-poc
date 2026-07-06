"""X / Twitter ingestion.

Free live scraping is dead in 2026 (Nitter shut down, X requires login for search),
so the default and reliable path is CSV IMPORT: drop exported/collected tweets into
fetch/twitter_import.csv (header: text,author,url,created_at,engagement) and they flow
through the same pipeline as every other source.

Mode via config.TWITTER_MODE (env TWITTER_MODE):
  csv    -> import the CSV only (default; fast, reliable)
  scrape -> try the free Nitter scraper only (usually returns 0)
  auto   -> scrape, then fall back to CSV
"""
import os, csv, hashlib
from config import TWITTER_QUERIES, TWITTER_MODE, FETCH_LIMITS

CSV_PATH = os.getenv("TWITTER_CSV", os.path.join(os.path.dirname(__file__), "twitter_import.csv"))


def _sid(url, text):
    """Stable id so re-importing the same tweet doesn't create duplicates."""
    seg = (url or "").rstrip("/").split("/")[-1]
    if seg.isdigit():
        return f"x:{seg}"
    return "x:" + hashlib.md5((text or url or "").encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def _from_csv():
    if not os.path.exists(CSV_PATH):
        print(f"  [twitter] no CSV at {CSV_PATH} — skipping")
        return []
    rows = []
    with open(CSV_PATH, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            text = (r.get("text") or "").strip()
            if not text:
                continue
            url = (r.get("url") or "").strip()
            rows.append(dict(
                source_id=_sid(url, text), source="twitter",
                author=(r.get("author") or "").strip(), text=text, url=url,
                created_at=(r.get("created_at") or "").strip(),
                engagement=int(r.get("engagement") or 0), lang="en"))
    print(f"  [twitter] {len(rows)} imported from CSV ({os.path.basename(CSV_PATH)})")
    return rows


def _scrape():
    rows = []
    try:
        from ntscraper import Nitter
    except ImportError:
        print("  [twitter] ntscraper not installed")
        return rows
    try:
        n = Nitter(log_level=0)
    except Exception as e:
        print(f"  [twitter] no working Nitter instance: {str(e)[:80]}")
        return rows
    for q in TWITTER_QUERIES:
        try:
            res = n.get_tweets(q, mode="term", number=FETCH_LIMITS["twitter"])
            for t in res.get("tweets", []):
                link = t.get("link", "") or ""
                stats = t.get("stats", {}) or {}
                rows.append(dict(
                    source_id=_sid(link, t.get("text", "")), source="twitter",
                    author=(t.get("user", {}) or {}).get("username", ""),
                    text=t.get("text", ""), url=link, created_at=t.get("date", ""),
                    engagement=int(stats.get("likes", 0) or 0), lang="en"))
        except Exception as e:
            print(f"  [twitter] query '{q}' failed: {str(e)[:80]}")
    return rows


def fetch():
    mode = (TWITTER_MODE or "csv").lower()
    if mode == "csv":
        return _from_csv()
    if mode == "scrape":
        return _scrape()
    rows = _scrape()                      # auto
    return rows if rows else _from_csv()
