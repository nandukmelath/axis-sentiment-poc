"""ScrapeBadger Twitter/X API — the preferred X source (paid, ToS-clean, no browser).
Advanced tweet search with cursor pagination; captures rich per-tweet fields and the
REAL post datetime (parsed to ISO).

Auth: SCRAPEBADGER_API_KEY in .env.  Endpoint: GET /v1/twitter/tweets/advanced_search (x-api-key).

Run:
  python -m fetch.scrapebadger                        # recent Axis mentions -> raw_posts
  python -m fetch.scrapebadger backfill --days 365 --query "(@AxisBank OR @AxisBankSupport)"
"""
import os, json, time, datetime, argparse
import requests
from config import SB_QUERY, SB_PAGES, SB_QUERY_TYPE, X_BACKFILL_DAYS, X_BACKFILL_WINDOW

BASE = "https://scrapebadger.com/v1/twitter/tweets/advanced_search"


class CreditsExhausted(Exception):
    pass


def _has_key():
    return bool(os.getenv("SCRAPEBADGER_API_KEY"))


def _key():
    k = os.getenv("SCRAPEBADGER_API_KEY")
    if not k:
        raise RuntimeError("SCRAPEBADGER_API_KEY not set (.env)")
    return k


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _iso(s):
    """Twitter time 'Wed Oct 10 20:19:24 +0000 2018' -> ISO 8601. Leave ISO/others as-is."""
    if not s:
        return ""
    try:
        return datetime.datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").isoformat()
    except (ValueError, TypeError):
        return s


def _row(t):
    uid = t.get("username") or ""
    tid = str(t.get("id") or "")
    return dict(
        source_id=f"x:{tid}", source="twitter",
        author=("@" + uid) if uid else "", author_name=t.get("user_name"),
        text=t.get("full_text") or t.get("text") or "",
        url=f"https://x.com/{uid}/status/{tid}" if (uid and tid) else "",
        created_at=_iso(t.get("created_at", "")), lang=t.get("lang", "en"),
        engagement=_int(t.get("favorite_count")),
        reply_count=_int(t.get("reply_count")), retweet_count=_int(t.get("retweet_count")),
        quote_count=_int(t.get("quote_count")), view_count=_int(t.get("view_count")),
        bookmark_count=_int(t.get("bookmark_count")), conversation_id=t.get("conversation_id"),
        raw_json=json.dumps(t, ensure_ascii=False))


def _get(params, retries=6):
    """GET with 429 backoff; raise CreditsExhausted on 402."""
    for attempt in range(retries):
        r = requests.get(BASE, headers={"x-api-key": _key()}, params=params, timeout=45)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            wait = min(2 ** attempt + 2, 40)
            print(f"    rate-limited (429), waiting {wait}s ...")
            time.sleep(wait)
            continue
        if r.status_code == 402:
            raise CreditsExhausted()
        print(f"  [scrapebadger] HTTP {r.status_code}: {r.text[:120]}")
        return None
    return None


def search(query, query_type="Latest", max_pages=5, count=100, sleep=1.2, upsert=False):
    from db import upsert_posts
    rows, cursor = {}, None
    for _ in range(max_pages):
        params = {"query": query, "query_type": query_type, "count": count}
        if cursor:
            params["cursor"] = cursor
        j = _get(params)
        if not j:
            break
        data = j.get("data") or []
        page = [_row(t) for t in data if t.get("id")]
        for pr in page:
            rows[pr["source_id"]] = pr
        if upsert and page:
            upsert_posts(page)   # incremental so backfill progress persists
        cursor = j.get("next_cursor")
        if not data or not cursor:
            break
        time.sleep(sleep)
    return list(rows.values())


def fetch():
    """Recent Axis mentions — used by run_fetch / streaming producer."""
    if not _has_key():
        print("  [scrapebadger] SCRAPEBADGER_API_KEY not set — skipping (no X source).")
        return []
    rows = search(SB_QUERY, SB_QUERY_TYPE, max_pages=SB_PAGES)
    print(f"  [scrapebadger] {len(rows)}")
    return rows


def _windows(days, window):
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)
    cur = end
    while cur > start:
        prev = max(start, cur - datetime.timedelta(days=window))
        yield prev.isoformat(), cur.isoformat()
        cur = prev


def backfill(days, window, pages, query=None):
    from db import init_db
    init_db()
    if not _has_key():
        print("  [scrapebadger] SCRAPEBADGER_API_KEY not set — skipping backfill (no X source configured).")
        return 0
    query = query or SB_QUERY
    wins = list(_windows(days, window))
    print(f"scrapebadger backfill: {len(wins)} windows of {window}d over {days}d\n  query: {query}")
    total, empty_streak = 0, 0
    for i, (since, until) in enumerate(wins, 1):
        q = f"{query} since:{since} until:{until}"
        try:
            rows = search(q, "Latest", max_pages=pages, upsert=True)
        except CreditsExhausted:
            print("  !! API credits exhausted — stopping. (top up ScrapeBadger to continue)")
            break
        total += len(rows)
        empty_streak = empty_streak + 1 if not rows else 0
        print(f"  [{i}/{len(wins)}] {since}..{until}: +{len(rows)} (running {total})")
        if empty_streak >= 6:
            print("  6 empty windows in a row — search index likely exhausted for older dates. Stopping.")
            break
    print(f"\nbackfill done: {total} tweets landed (dedup by id). "
          f"Classify: python -m analyze.run_analyze")
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="fetch", choices=["fetch", "backfill"])
    ap.add_argument("--days", type=int, default=X_BACKFILL_DAYS)
    ap.add_argument("--window", type=int, default=X_BACKFILL_WINDOW)
    ap.add_argument("--pages", type=int, default=10)
    ap.add_argument("--query", type=str, default=None)
    a = ap.parse_args()
    if a.cmd == "backfill":
        backfill(a.days, a.window, a.pages, a.query)
    else:
        from db import init_db, upsert_posts
        init_db()
        rows = fetch()
        upsert_posts(rows)
        print(f"landed {len(rows)} -> raw_posts. Classify: python -m analyze.run_analyze")


if __name__ == "__main__":
    main()
