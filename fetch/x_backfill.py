"""Historical X backfill — steps back week-by-week over the past year using
since:/until: search operators, crawls each window, upserts into raw_posts.

HONEST LIMITS (read before running):
  - X search does NOT reliably return a COMPLETE year of history for free. You get a
    strong SAMPLE, not every post. Old-date completeness degrades without premium.
  - This makes MANY heavy scroll sessions -> X will rate-limit/flag the account.
    Use a BURNER account. Expect throttling; the polite sleep + smaller windows help.
  - Volume can be large. Classification then runs via the cascade (VADER free for most,
    Gemini throttled for the rest) and may take a long time on the free tier.

Run:
  python -m fetch.x_crawler login              # once, if not already logged in
  python -m fetch.x_backfill                   # default: 365 days, 7-day windows
  python -m fetch.x_backfill --days 90 --window 7 --scrolls 12
"""
import sys, os, time, datetime, argparse
from urllib.parse import quote
from config import (X_STATE_FILE, X_HEADLESS, X_BACKFILL_QUERY, X_BACKFILL_DAYS,
                    X_BACKFILL_WINDOW, X_BACKFILL_SCROLLS, X_BACKFILL_SLEEP)
from fetch.x_crawler import EXTRACT_JS, _norm, UA


def _windows(days, window):
    """Yield (since, until) date strings, newest window first, back `days`."""
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)
    cur = end
    while cur > start:
        prev = max(start, cur - datetime.timedelta(days=window))
        yield prev.isoformat(), cur.isoformat()
        cur = prev


def backfill(days, window, scrolls, sleep):
    if not os.path.exists(X_STATE_FILE):
        print(f"no X session ({X_STATE_FILE}). Run once: python -m fetch.x_crawler login")
        return 0
    from playwright.sync_api import sync_playwright
    from db import init_db, upsert_posts
    init_db()

    wins = list(_windows(days, window))
    print(f"backfill: {len(wins)} windows of {window}d over {days}d — query {X_BACKFILL_QUERY}")
    total = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=X_HEADLESS)
        ctx = browser.new_context(storage_state=X_STATE_FILE, user_agent=UA)
        page = ctx.new_page()
        for i, (since, until) in enumerate(wins, 1):
            q = f"{X_BACKFILL_QUERY} since:{since} until:{until}"
            url = f"https://x.com/search?q={quote(q)}&src=typed_query&f=live"
            rows = {}
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3500)
                if "/login" in page.url or page.query_selector('input[name="text"]'):
                    print("session expired — re-run: python -m fetch.x_crawler login")
                    break
                last = -1
                for _ in range(scrolls):
                    for it in page.evaluate(EXTRACT_JS):
                        rows[it["id"]] = _norm(it)
                    if len(rows) == last:      # no new tweets loading -> window exhausted
                        break
                    last = len(rows)
                    page.mouse.wheel(0, 3400)
                    page.wait_for_timeout(1500)
            except Exception as e:
                print(f"  [{since}..{until}] error: {str(e)[:80]}")
            if rows:
                upsert_posts(list(rows.values()))   # incremental: progress persists
                total += len(rows)
            print(f"  [{i}/{len(wins)}] {since}..{until}: +{len(rows)} (running {total})")
            time.sleep(sleep)
        browser.close()
    print(f"\nbackfill done: {total} X posts landed (dedup by id). "
          f"Classify with: python -m analyze.run_analyze   (or let the stream worker drain it)")
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=X_BACKFILL_DAYS)
    ap.add_argument("--window", type=int, default=X_BACKFILL_WINDOW)
    ap.add_argument("--scrolls", type=int, default=X_BACKFILL_SCROLLS)
    ap.add_argument("--sleep", type=float, default=X_BACKFILL_SLEEP)
    a = ap.parse_args()
    backfill(a.days, a.window, a.scrolls, a.sleep)


if __name__ == "__main__":
    main()
