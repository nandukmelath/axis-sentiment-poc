"""X.com authenticated crawler (Playwright).

X killed its free API and blocks anonymous search, so we drive a REAL logged-in
browser session. Flow:
  1) one-time login (headed browser, you log in manually):  python -m fetch.x_crawler login
     -> saves session cookies to X_STATE_FILE
  2) crawl (headless, reuses the session):                  python -m fetch.x_crawler
     -> searches each X_SEARCH_QUERIES on the 'Latest' tab, scrolls, extracts tweets,
        normalizes, and upserts into raw_posts. The stream worker then classifies them.

WARNINGS
  - Automated scraping violates X's ToS. Use a DEDICATED/BURNER account (ban risk).
  - Fragile: X changes its DOM and runs anti-bot detection. Expect occasional breakage.
  - x_state.json holds live session cookies — it is gitignored; keep it secret.
"""
import sys, os
from urllib.parse import quote
from config import X_SEARCH_QUERIES, X_STATE_FILE, X_SCROLLS, X_HEADLESS

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# runs in the page: pull tweets from the DOM
EXTRACT_JS = r"""
() => {
  const out = [];
  document.querySelectorAll('article[data-testid="tweet"]').forEach(a => {
    const t = a.querySelector('div[data-testid="tweetText"]');
    const text = t ? t.innerText : '';
    const link = a.querySelector('a[href*="/status/"]');
    const href = link ? link.href : '';
    const m = href.match(/\/([^\/]+)\/status\/(\d+)/);
    if (!text || !m) return;
    const timeEl = a.querySelector('time');
    let eng = 0;
    const grp = a.querySelector('[role="group"]');
    if (grp) {
      const nums = (grp.getAttribute('aria-label') || '').match(/\d[\d,\.]*/g) || [];
      eng = nums.reduce((s, n) => s + (parseInt(n.replace(/[^\d]/g, '')) || 0), 0);
    }
    out.push({ id: m[2], handle: m[1], text,
               href: `https://x.com/${m[1]}/status/${m[2]}`,
               dt: timeEl ? timeEl.getAttribute('datetime') : '', eng });
  });
  return out;
}
"""


def _norm(it):
    return dict(source_id=f"x:{it['id']}", source="twitter", author=it["handle"],
                text=it["text"], url=it["href"], created_at=it.get("dt", ""),
                engagement=int(it.get("eng", 0) or 0), lang="en")


def login():
    """Open a real browser, let the user log in, save the session."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()
        page.goto("https://x.com/login")
        input("\n>>> Log in to X in the browser window, then press Enter here to save the session... ")
        ctx.storage_state(path=X_STATE_FILE)
        browser.close()
        print(f"saved X session -> {X_STATE_FILE}")


def fetch(queries=None):
    """Crawl the latest posts for each query. Returns normalized rows."""
    if not os.path.exists(X_STATE_FILE):
        print(f"  [x] no session ({X_STATE_FILE}). Run once:  python -m fetch.x_crawler login")
        return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [x] skipped (pip install playwright && python -m playwright install chromium)")
        return []
    queries = queries or X_SEARCH_QUERIES
    rows = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=X_HEADLESS)
        ctx = browser.new_context(storage_state=X_STATE_FILE, user_agent=UA)
        page = ctx.new_page()
        for q in queries:
            url = f"https://x.com/search?q={quote(q)}&src=typed_query&f=live"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3500)
                if "/login" in page.url or page.query_selector('input[name="text"]'):
                    print("  [x] session expired — re-run: python -m fetch.x_crawler login")
                    break
                for _ in range(X_SCROLLS):
                    for it in page.evaluate(EXTRACT_JS):
                        rows[it["id"]] = _norm(it)
                    page.mouse.wheel(0, 3200)
                    page.wait_for_timeout(1500)
                print(f"  [x] '{q}': {len(rows)} total so far")
            except Exception as e:
                print(f"  [x] '{q}' error: {str(e)[:90]}")
        browser.close()
    return list(rows.values())


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        login()
        return
    from db import init_db, upsert_posts
    init_db()
    rows = fetch()
    n = upsert_posts(rows)
    print(f"crawled {len(rows)} X posts -> raw_posts. Next: worker/analyze will classify them.")


if __name__ == "__main__":
    main()
